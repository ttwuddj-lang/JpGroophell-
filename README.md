# Group Help Bot — MongoDB Edition

This version stores group settings, filters, locks, approvals/free users, warnings, FedBan data and Today/Week/Overall chat rankings in MongoDB.

## Railway Variables
- BOT_TOKEN
- OWNER_ID
- MONGO_URI
- MONGO_DB (optional; defaults to group_help_bot)
- START_PHOTO (optional)
- SUPPORT_URL
- OWNER_URL
- CHANNEL_URL

Use a persistent MongoDB service/cluster. Do not remove the MongoDB database when redeploying the bot.

## Important
Changing/redeploying the bot code does not erase MongoDB data. The bot uses upserts and indexes rather than recreating the database on startup.
