## BURN-IN 1.0.4

**Fixed**

- No more crawl after starting. It used to resume every clip the last session left unfinished,
  however old - dozens at once - which flooded the queue and stalled every capture for minutes.
  Clips older than 15 minutes are now written off before they are queued.

Also in this line of releases: the live viewer plays again, storage no longer fills up in hours,
and titles hook instead of narrate. The full history is in
[CHANGELOG.md](https://github.com/Saizama-cmyk/BURN-IN-CLIP-BOT/blob/main/CHANGELOG.md).
