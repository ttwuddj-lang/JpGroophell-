# Group Help Bot — MongoDB Fixed Edition

This version fixes the previously reported issues and keeps data in MongoDB across Railway restarts/redeploys.

## Railway variables

```text
BOT_TOKEN=your_bot_token
OWNER_ID=your_numeric_telegram_user_id
MONGO_URI=your_mongodb_connection_string
MONGO_DB=group_help_bot
START_PHOTO=https://example.com/start.jpg
SUPPORT_URL=https://t.me/YourSupport
OWNER_URL=https://t.me/YourUsername
CHANNEL_URL=https://t.me/YourChannel

SIGHTENGINE_API_USER=your_moderation_api_user
SIGHTENGINE_API_SECRET=your_moderation_api_secret
NSFW_THRESHOLD=0.80
NSFW_REMOVE_PARTIAL=false
```

### Start photo
`START_PHOTO` accepts either a Telegram file_id or a direct public HTTP(S) image URL. The URL must return the actual image, not a webpage.

### Filters
1. Admin: `/filter jpexo`
2. Bot asks for the response.
3. Admin sends the sticker/photo/video/text/etc. to use as the response.
4. A user typing `jpexo` gets that saved response. The trigger message is not deleted by the filter system.

### Welcome
Admin/owner can use `/setwelcome` with custom HTML-style formatting supported by Telegram, plus:
- `{mention}`
- `{id}`
- `{group}`
- `{count}`
- `{username}`
- `{first}`
- `{last}`

Then `/welcome on`.

### FedBan
Only `OWNER_ID` can run `/fedban` and `/unfedban`. A user in the FedBan list is automatically banned in groups where this bot has permission to ban members. Group admins cannot remove a FedBan.

### Ranking
`/rank today`, `/rank week`, `/rank overall` shows a chart image with top 10 chatters. Each normal chat message counts once. Five messages within one second blocks that user's ranking count for 10 minutes.

### Broadcast
Only `OWNER_ID` can use `/broadcast text` or reply to a message with `/broadcast`. The bot sends it to every group stored in MongoDB.

### NSFW moderation
`/nsfw on` enables automated media checking for photos, videos, GIF/animations and static stickers. The bot uses the configured moderation API and deletes media when its configured threshold is exceeded. If the API credentials are missing, `/nsfw on` warns that detection is not active.
