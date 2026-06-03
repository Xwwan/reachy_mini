"""Entrypoint for the Reachy Mini conversation app."""

import os
import sys
import time
import asyncio
import argparse
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
from fastapi import FastAPI
from fastrtc import Stream
from gradio.utils import get_space

from reachy_mini import ReachyMini, ReachyMiniApp
from cookAIware.data_store import resolve_data_dir
from cookAIware.utils import (
    parse_args,
    setup_logger,
    handle_vision_stuff,
    log_connection_troubleshooting,
)


def update_chatbot(chatbot: List[Dict[str, Any]], response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Update the chatbot with AdditionalOutputs."""
    chatbot.append(response)
    return chatbot


def main() -> None:
    """Entrypoint for the Reachy Mini conversation app."""
    args, _ = parse_args()
    run(args)


def run(
    args: argparse.Namespace,
    robot: ReachyMini = None,
    app_stop_event: Optional[threading.Event] = None,
    settings_app: Optional[FastAPI] = None,
    instance_path: Optional[str] = None,
) -> None:
    """Run the Reachy Mini conversation app."""
    # Putting these dependencies here makes the dashboard faster to load when the conversation app is installed
    from cookAIware.moves import MovementManager
    from cookAIware.console import LocalStream
    from cookAIware.openai_realtime import OpenaiRealtimeHandler
    from cookAIware.tools.core_tools import ToolDependencies
    from cookAIware.audio.head_wobbler import HeadWobbler

    logger = setup_logger(args.debug)
    logger.info("Starting Reachy Mini Conversation App")

    if args.no_camera and args.head_tracker is not None:
        logger.warning("Head tracking disabled: --no-camera flag is set. Remove --no-camera to enable head tracking.")

    if robot is None:
        try:
            robot_kwargs = {}
            if args.robot_name is not None:
                robot_kwargs["robot_name"] = args.robot_name

            logger.info("Initializing ReachyMini (SDK will auto-detect appropriate backend)")
            robot = ReachyMini(**robot_kwargs)

        except TimeoutError as e:
            logger.error(f"Connection timeout: Failed to connect to Reachy Mini daemon. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except ConnectionError as e:
            logger.error(f"Connection failed: Unable to establish connection to Reachy Mini. Details: {e}")
            log_connection_troubleshooting(logger, args.robot_name)
            sys.exit(1)

        except Exception as e:
            logger.error(f"Unexpected error during robot initialization: {type(e).__name__}: {e}")
            logger.error("Please check your configuration and try again.")
            sys.exit(1)

    # Check if running in simulation mode without --gradio
    if robot.client.get_status()["simulation_enabled"] and not args.gradio:
        logger.error(
            "Simulation mode requires Gradio interface. Please use --gradio flag when running in simulation mode."
        )
        robot.client.disconnect()
        sys.exit(1)

    camera_worker, _, vision_manager = handle_vision_stuff(args, robot)

    movement_manager = MovementManager(
        current_robot=robot,
        camera_worker=camera_worker,
    )

    enable_motion = False
    head_wobbler = None
    movement_manager.idle_inactivity_delay = 9999.0

    data_dir = resolve_data_dir(instance_path)
    deps = ToolDependencies(
        reachy_mini=robot,
        movement_manager=movement_manager,
        camera_worker=camera_worker,
        vision_manager=vision_manager,
        head_wobbler=head_wobbler,
        data_dir=data_dir,
    )
    current_file_path = os.path.dirname(os.path.abspath(__file__))
    logger.debug(f"Current file absolute path: {current_file_path}")
    chatbot = gr.Chatbot(
        type="messages",
        resizable=True,
        avatar_images=(
            os.path.join(current_file_path, "images", "user_avatar.png"),
            os.path.join(current_file_path, "images", "reachymini_avatar.png"),
        ),
    )
    logger.debug(f"Chatbot avatar images: {chatbot.avatar_images}")

    handler = OpenaiRealtimeHandler(deps, gradio_mode=args.gradio, instance_path=instance_path)
    deps.handler = handler

    stream_manager: gr.Blocks | LocalStream | None = None
    gradio_app: FastAPI | None = None

    if args.gradio:
        has_env_key = bool(os.getenv("OPENAI_API_KEY")) and not get_space()
        api_key_textbox = gr.Textbox(
            label="OPENAI API Key (use .env or secrets)",
            type="password",
            value=os.getenv("OPENAI_API_KEY") if has_env_key else "",
            visible=not has_env_key,
        )
        if has_env_key:
            gr.Markdown("OpenAI API key loaded from `.env`. The input is hidden for safety.")

        from cookAIware.gradio_personality import PersonalityUI

        personality_ui = PersonalityUI()
        personality_ui.create_components()

        stream = Stream(
            handler=handler,
            mode="send-receive",
            modality="audio",
            additional_inputs=[
                chatbot,
                api_key_textbox,
                *personality_ui.additional_inputs_ordered(),
            ],
            additional_outputs=[chatbot],
            additional_outputs_handler=update_chatbot,
            ui_args={"title": "Talk with Reachy Mini"},
        )
        stream_manager = stream.ui
        if not settings_app:
            app = FastAPI()
        else:
            app = settings_app

        personality_ui.wire_events(handler, stream_manager)

        def _plan_to_markdown(plan: Dict[str, Any]) -> str:
            if not plan:
                return "No meal plan available."
            lines = [f"Week starting {plan.get('week_start', '')}"]
            for day in plan.get("days", []):
                lines.append(f"\n**{day.get('day', '').capitalize()} ({day.get('date', '')})**")
                for meal in day.get("meals", []):
                    lines.append(f"- {meal.get('meal')}: {meal.get('name')}")
            return "\n".join(lines)

        def _list_to_markdown(items: List[Dict[str, Any]]) -> str:
            if not items:
                return "No shopping list items."
            lines = []
            for item in items:
                name = item.get("name", "")
                qty = item.get("quantity", 0)
                unit = item.get("unit", "")
                lines.append(f"- {name}: {qty} {unit}")
            return "\n".join(lines)

        def _load_profile() -> Tuple[int | None, int | None]:
            from cookAIware.family_profile import load_profile

            profile = load_profile(data_dir)
            return profile.get("adults"), profile.get("children")

        def _save_profile(adults: int | None, children: int | None) -> str:
            from cookAIware.family_profile import update_profile

            update_profile(data_dir, adults, children, None)
            return "Profile saved."

        def _load_inventory() -> List[List[Any]]:
            from cookAIware.inventory import list_inventory

            items = list_inventory(data_dir)
            rows = []
            for item in items:
                rows.append(
                    [
                        item.get("display_name") or item.get("name", ""),
                        item.get("quantity", 0),
                        item.get("unit", ""),
                        item.get("expiration_date") or "",
                        item.get("storage_location") or "",
                    ]
                )
            return rows

        def _save_inventory(rows: List[List[Any]]) -> str:
            from cookAIware.inventory import normalize_items_for_save, save_inventory

            items = []
            for row in rows or []:
                if not row:
                    continue
                name = str(row[0]).strip() if len(row) > 0 else ""
                if not name:
                    continue
                items.append(
                    {
                        "name": name,
                        "quantity": row[1] if len(row) > 1 else None,
                        "unit": row[2] if len(row) > 2 else None,
                        "expiration_date": row[3] if len(row) > 3 else None,
                        "storage_location": row[4] if len(row) > 4 else None,
                    }
                )
            normalized = normalize_items_for_save(items)
            save_inventory(data_dir, normalized)
            return "Inventory saved."

        def _generate_plan() -> str:
            from cookAIware.inventory import list_inventory
            from cookAIware.family_profile import load_profile
            from cookAIware.meal_planner import generate_plan

            profile = load_profile(data_dir)
            adults = profile.get("adults")
            children = profile.get("children")
            if adults is None or children is None:
                return "Set family profile first."
            inventory = list_inventory(data_dir)
            plan = generate_plan(data_dir, inventory, int(adults), int(children), profile.get("schedule"))
            return _plan_to_markdown(plan)

        def _load_plan() -> str:
            from cookAIware.meal_planner import load_plan

            return _plan_to_markdown(load_plan(data_dir))

        def _generate_shopping_list() -> Tuple[List[List[Any]], str]:
            from cookAIware.meal_planner import load_plan
            from cookAIware.inventory import list_inventory
            from cookAIware.shopping_list import generate_from_plan

            plan = load_plan(data_dir)
            inventory = list_inventory(data_dir)
            items = generate_from_plan(data_dir, plan, inventory)
            rows = [[item.get("name"), item.get("quantity"), item.get("unit")] for item in items]
            return rows, _list_to_markdown(items)

        def _load_shopping_list() -> Tuple[List[List[Any]], str]:
            from cookAIware.shopping_list import list_items

            items = list_items(data_dir)
            rows = [[item.get("name"), item.get("quantity"), item.get("unit")] for item in items]
            return rows, _list_to_markdown(items)

        def _save_shopping_list(rows: List[List[Any]]) -> str:
            from cookAIware.shopping_list import save_list

            items = []
            for row in rows or []:
                if not row:
                    continue
                name = str(row[0]).strip() if len(row) > 0 else ""
                if not name:
                    continue
                items.append({"name": name, "quantity": row[1] or 0, "unit": row[2] or "pcs"})
            save_list(data_dir, items)
            return "Shopping list saved."

        async def _send_command(text: str) -> str:
            if not text.strip():
                return "Enter a command."
            ok = await handler.send_text(text)
            return "Sent." if ok else "Failed to send."

        hero_image_path = str(Path(__file__).parent / "images" / "cookaiware-hero.png")

        with stream.ui:
            with gr.Tab("CookAIware"):
                gr.Image(value=hero_image_path, show_label=False)
                gr.Markdown("Manage inventory, profile, meal plan, and shopping list.")

                with gr.Row():
                    adults = gr.Number(label="Adults", precision=0)
                    children = gr.Number(label="Children", precision=0)
                    profile_status = gr.Textbox(label="Profile status", interactive=False)

                with gr.Row():
                    load_profile_btn = gr.Button("Load profile")
                    save_profile_btn = gr.Button("Save profile")

                inventory_df = gr.Dataframe(
                    headers=["name", "quantity", "unit", "expiration_date", "storage_location"],
                    datatype=["str", "number", "str", "str", "str"],
                    label="Inventory",
                    interactive=True,
                    row_count=(0, "dynamic"),
                )
                inventory_status = gr.Textbox(label="Inventory status", interactive=False)
                with gr.Row():
                    load_inventory_btn = gr.Button("Load inventory")
                    save_inventory_btn = gr.Button("Save inventory")

                plan_md = gr.Markdown()
                with gr.Row():
                    load_plan_btn = gr.Button("Load meal plan")
                    generate_plan_btn = gr.Button("Generate meal plan")

                shopping_df = gr.Dataframe(
                    headers=["name", "quantity", "unit"],
                    datatype=["str", "number", "str"],
                    label="Shopping list",
                    interactive=True,
                    row_count=(0, "dynamic"),
                )
                shopping_md = gr.Markdown()
                shopping_status = gr.Textbox(label="Shopping status", interactive=False)
                with gr.Row():
                    load_shopping_btn = gr.Button("Load shopping list")
                    generate_shopping_btn = gr.Button("Generate shopping list")
                    save_shopping_btn = gr.Button("Save shopping list")

                command_text = gr.Textbox(label="Send a command", placeholder="Plan our meals for this week")
                send_command_btn = gr.Button("Send")
                command_status = gr.Textbox(label="Command status", interactive=False)

                load_profile_btn.click(_load_profile, outputs=[adults, children])
                save_profile_btn.click(_save_profile, inputs=[adults, children], outputs=[profile_status])
                load_inventory_btn.click(_load_inventory, outputs=[inventory_df])
                save_inventory_btn.click(_save_inventory, inputs=[inventory_df], outputs=[inventory_status])
                load_plan_btn.click(_load_plan, outputs=[plan_md])
                generate_plan_btn.click(_generate_plan, outputs=[plan_md])
                load_shopping_btn.click(_load_shopping_list, outputs=[shopping_df, shopping_md])
                generate_shopping_btn.click(_generate_shopping_list, outputs=[shopping_df, shopping_md])
                save_shopping_btn.click(_save_shopping_list, inputs=[shopping_df], outputs=[shopping_status])
                send_command_btn.click(_send_command, inputs=[command_text], outputs=[command_status])
    else:
        # In headless mode, wire settings_app + instance_path to console LocalStream
        stream_manager = LocalStream(
            handler,
            robot,
            settings_app=settings_app,
            instance_path=instance_path,
        )

    # Each async service → its own thread/loop
    if enable_motion:
        movement_manager.start()
        if head_wobbler:
            head_wobbler.start()
    if camera_worker:
        camera_worker.start()
    if vision_manager:
        vision_manager.start()

    def poll_stop_event() -> None:
        """Poll the stop event to allow graceful shutdown."""
        if app_stop_event is not None:
            app_stop_event.wait()

        logger.info("App stop event detected, shutting down...")
        if stream_manager is not None:
            try:
                stream_manager.close()
            except Exception as e:
                logger.error(f"Error while closing stream manager: {e}")

    if app_stop_event:
        threading.Thread(target=poll_stop_event, daemon=True).start()

    try:
        if stream_manager is None:
            raise RuntimeError("Stream manager not initialized.")
        stream_manager.launch()
    except KeyboardInterrupt:
        logger.info("Keyboard interruption in main thread... closing server.")
    finally:
        if enable_motion:
            movement_manager.stop()
            if head_wobbler:
                head_wobbler.stop()
        if camera_worker:
            camera_worker.stop()
        if vision_manager:
            vision_manager.stop()

        # Ensure media is explicitly closed before disconnecting
        try:
            robot.media.close()
        except Exception as e:
            logger.debug(f"Error closing media during shutdown: {e}")

        # prevent connection to keep alive some threads
        robot.client.disconnect()
        time.sleep(1)
        logger.info("Shutdown complete.")


class Cookaiware(ReachyMiniApp):  # type: ignore[misc]
    """Reachy Mini Apps entry point for the conversation app."""

    custom_app_url = "http://0.0.0.0:7860/"
    dont_start_webserver = False

    def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
        """Run the Reachy Mini conversation app."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        args, _ = parse_args()
        args.gradio = False

        # is_wireless = reachy_mini.client.get_status()["wireless_version"]
        # args.head_tracker = None if is_wireless else "mediapipe"

        instance_path = self._get_instance_path().parent
        run(
            args,
            robot=reachy_mini,
            app_stop_event=stop_event,
            settings_app=self.settings_app,
            instance_path=instance_path,
        )


if __name__ == "__main__":
    app = Cookaiware()
    try:
        app.wrapped_run()
    except KeyboardInterrupt:
        app.stop()
