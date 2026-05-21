import curses
import gzip
import json
import locale
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Frame:
    lines: tuple[str, ...]

    @property
    def height(self):
        return len(self.lines)

    @property
    def width(self):
        return max((len(line) for line in self.lines), default=0)


@dataclass(frozen=True)
class AnimationClip:
    frames: tuple[Frame, ...]
    frame_seconds: float
    path: Path

    @classmethod
    def load(cls, path):
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            payload = json.load(handle)

        if payload.get('version') != 1:
            raise ValueError(f'Unsupported terminal animation format: {path}')

        frame_seconds = float(payload['frame_seconds'])
        if frame_seconds <= 0:
            raise ValueError(f'Invalid frame duration in {path}')

        frames = tuple(Frame(tuple(text.splitlines())) for text in payload['frames'])
        if not frames:
            raise ValueError(f'Animation has no frames: {path}')

        return cls(frames=frames, frame_seconds=frame_seconds, path=path)


def centered_origin(term_width, term_height, frame_width, frame_height):
    return (
        (term_width - frame_width) // 2,
        (term_height - frame_height) // 2,
    )


def visible_line_slice(line, x, term_width):
    if term_width <= 0 or not line:
        return None

    left = max(x, 0)
    right = min(x + len(line), term_width)
    if left >= right:
        return None

    source_left = max(-x, 0)
    source_right = source_left + (right - left)
    return left, line[source_left:source_right]


class CursesRenderer:
    def __init__(self, screen):
        self.screen = screen
        self.screen.nodelay(True)
        self.screen.keypad(True)
        self.screen.leaveok(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass

    def close(self):
        self.screen.leaveok(False)
        try:
            curses.curs_set(1)
        except curses.error:
            pass

    def draw(self, frame):
        self._drain_input()
        term_height, term_width = self.screen.getmaxyx()
        x, y = centered_origin(term_width, term_height, frame.width, frame.height)

        self.screen.erase()
        for line_index, line in enumerate(frame.lines):
            draw_y = y + line_index
            if draw_y < 0 or draw_y >= term_height:
                continue

            visible = visible_line_slice(line, x, term_width)
            if not visible:
                continue

            draw_x, text = visible
            try:
                self.screen.addstr(draw_y, draw_x, text)
            except curses.error:
                # Some curses implementations reject writing the final cell.
                if draw_x + len(text) == term_width and len(text) > 1:
                    self.screen.addstr(draw_y, draw_x, text[:-1])

        self.screen.noutrefresh()
        curses.doupdate()

    def play_once(self, clip, stop_event):
        for frame in clip.frames:
            if stop_event.is_set():
                return
            self.draw(frame)
            self._wait(clip.frame_seconds, stop_event)

    def play_loop_until_event(self, clip, stop_event, interrupt_event):
        while not stop_event.is_set() and not interrupt_event.is_set():
            for frame in clip.frames:
                if stop_event.is_set() or interrupt_event.is_set():
                    return
                self.draw(frame)
                self._wait(clip.frame_seconds, stop_event, interrupt_event)

    def _wait(self, seconds, stop_event, interrupt_event=None):
        deadline = time.monotonic() + seconds
        while not stop_event.is_set():
            if interrupt_event and interrupt_event.is_set():
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            stop_event.wait(timeout=min(remaining, 0.03))

    def _drain_input(self):
        while True:
            key = self.screen.getch()
            if key == -1:
                return
            if key == curses.KEY_RESIZE:
                try:
                    curses.update_lines_cols()
                except curses.error:
                    pass


def run_curses(loop):
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error:
        pass

    def wrapped(screen):
        renderer = CursesRenderer(screen)
        try:
            loop(renderer)
        finally:
            renderer.close()

    return curses.wrapper(wrapped)
