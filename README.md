# Group Help Bot — Approve + Free lock system

### Lock / Approve / Free
- `/lock sticker|gif|emoji|photo|video|link` locks that content type.
- `/approve` by replying to a user (or `/approve USER_ID`) allows that user to send locked sticker/gif/photo/video/emoji and use configured ban words.
- `/free` by replying to a user (or `/free USER_ID`) gives the same exemption from ban words and non-link locks.
- **Link lock always remains active**, even for approved/free users.
- `/unapprove` and `/unfree` remove the corresponding status.
- NSFW moderation remains active even for approved/free users.

Admin/owner permissions are required for these commands.
