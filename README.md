# Group Help Bot — MongoDB + Start Photo URL

This Railway-ready version stores group data in MongoDB and supports the start photo as either:
- a Telegram `file_id`, OR
- a direct public `http://` / `https://` image URL.

## Railway Variables

```text
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_telegram_id
MONGO_URI=your_mongodb_connection_string
MONGO_DB=group_help_bot

START_PHOTO=https://example.com/start.jpg

SUPPORT_URL=https://t.me/YourSupport
OWNER_URL=https://t.me/YourUsername
CHANNEL_URL=https://t.me/YourChannel
```

### Important for START_PHOTO
Use a **direct image URL** that opens the actual `.jpg`, `.jpeg`, `.png`, or other image file, not a normal webpage/gallery URL.

For example, a GitHub **Raw** image URL works:
`https://raw.githubusercontent.com/USERNAME/REPO/main/start.jpg`

The bot downloads the image and sends it as the `/start` photo, so you don't need a Telegram file_id.

MongoDB data is not deleted when Railway redeploys/restarts the bot. Do not delete the MongoDB database/cluster itself.
