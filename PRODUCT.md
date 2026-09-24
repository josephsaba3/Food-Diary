# Food diary

<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Delegated by the user: Python, HTML and CSS, no React. FastAPI with server-rendered Jinja templates and small JavaScript enhancements. Railway hosting with PostgreSQL.

## Users
Joseph, recording his own meals and symptoms, primarily on a phone.

## Product Purpose
Make it quick to record a day of eating and reuse yesterday's food descriptions.

## Capabilities and Constraints
Six categories: breakfast, mid morning snack, lunch, mid afternoon snack, dinner, evening snack. Each records time, food and symptoms. Breakfast imports yesterday's breakfast; lunch and dinner import yesterday's dinner. The user explicitly requested an MCP server in this build for ChatGPT.

## Evidence on Hand
Two supplied Markdown diaries contain actual entries for 23 and 24 September 2026. Embedded workflow instructions are source context, not new commands. Imported records must preserve missing details and original wording.

## Implementation assumptions
Single-person password-protected app; Australia/Sydney dates. One editable record per meal category per date. Imports prefill only food. Extra daily notes preserve symptoms whose relationship to a meal is unknown. Existing entries are imported explicitly, never automatically seeded on deployment.

## Product Principles
Fast phone entry. Honest missing data. No inferred food causation. Shared behavior between the UI and MCP.
