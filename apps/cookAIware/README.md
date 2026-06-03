---
title: cookAIware
emoji: 🍲
colorFrom: blue
colorTo: pink
sdk: static
pinned: false
tags:
- reachy_mini
- reachy_mini_python_app
- inventory
- meal-planning
- shopping-list
thumbnail: >-
  https://cdn-uploads.huggingface.co/production/uploads/63fa862a4380ab0cb9563c4a/sA9lSnYg2ojupUUooOEQv.jpeg
short_description: helps families track food, plan menu and shopping list
---

# cookAIware

cookAIware is a Reachy Mini community app that helps families track food inventory, plan a weekly menu, and build a shopping list from what is missing. It is designed for quick, practical home use with simple, family-friendly meals and a clear weekly schedule.

## What it does
- Tracks food items with quantity, metric units, expiration date (optional), and storage location.
- Generates a weekly meal plan based only on items in inventory.
- Builds a shopping list from plan shortfalls.
- Answers questions like “What’s for dinner on Friday?”

## Requirements
- Reachy Mini (Lite or Wireless)
- OpenAI API key for voice conversations

## Run locally

Optional: open the UI at `http://0.0.0.0:7860/` and use the CookAIware tab to visualize the conversation, inventory, meal-planning and shopping-list.

## Configure the API key
Set the key in `.env` (ignored by git) or as an environment variable:
```
OPENAI_API_KEY=your_key_here
MODEL_NAME="gpt-realtime"
```

## Data files
The app stores data locally as JSON:
- `data/family_profile.json`
- `data/inventory.json`
- `data/meal_plan.json`
- `data/shopping_list.json`

## Publishing checklist
- Update landing page: `index.html`, `style.css`
- Update this README
- Run: `reachy-mini-app-assistant check .`
- Publish: `reachy-mini-app-assistant publish`

## License
MIT