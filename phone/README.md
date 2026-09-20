# BURN-IN Remote (iPhone)

A native app that runs your BURN-IN desk from your phone: status, streams, clips, post-now,
pause and the log. It talks to BURN-IN on your PC over your own Wi-Fi — nothing goes through the
internet, and there is no account.

## Get the file

Every build is published as a release, and one URL always points at the newest one:

**https://github.com/Saizama-cmyk/BURN-IN-CLIP-BOT/releases/download/phone-latest/BURN-IN-Remote.ipa**

It is unsigned on purpose — your sideloader signs it with your own Apple ID, which is what makes
sideloading work without a paid developer account.

## Install it (pick the tool you already use)

| You are on | Tool | Notes |
|---|---|---|
| Windows | [AltStore](https://altstore.io) (AltServer) | Auto-refreshes over Wi-Fi every 7 days |
| Windows | [Sideloadly](https://sideloadly.io) | Simple drag-and-drop; re-install by hand |
| macOS | AltStore or Sideloadly | Same as above |
| Linux | [SideStore](https://sidestore.io) | Pairs on-device; no desktop server needed |
| Any | [Feather](https://github.com/khcrysalis/Feather) (on-device) | Installs and updates from a source |
| Jailbroken / TrollStore | ESign, TrollStore | Permanent signing, no 7-day limit |

With a free Apple ID any sideloaded app stops working after 7 days until it is re-signed. AltStore
and SideStore do that automatically in the background; the others need a re-install. That is
Apple's rule, not this app's.

## Live updates, like the desktop app

Add this **source** once and new builds appear as updates in the app list:

```
https://raw.githubusercontent.com/Saizama-cmyk/BURN-IN-CLIP-BOT/main/docs/sideload.json
```

Works in AltStore, SideStore, Feather and anything else that reads the standard source format.
For tools without sources (Sideloadly, ESign), re-download the `phone-latest` link above — it
always serves the newest build.

## Connect it

1. On the PC: **Settings → Dashboard → Phone remote**, then restart BURN-IN.
2. The Live desk grows a **Phone remote** button showing the address, e.g. `192.168.0.72:8787`.
3. In the app, type that address and your profile password. The session token is kept in the iOS
   keychain; the password is never stored.

## If it cannot connect

- Phone and PC must be on the same network (not guest Wi-Fi, not cellular).
- BURN-IN must be running with Phone remote switched on.
- Allow BURN-IN through the firewall on private networks when Windows asks.
- Your PC's address can change when the router reissues it — retype it, or give the PC a reserved
  address in your router.

## Building it yourself

Apple's toolchain only runs on macOS, so the repo builds it on a free GitHub macOS runner:
Actions → **Phone app (.ipa)** → Run workflow. The finished `.ipa` is attached to the run and to
the `phone-latest` release.

Locally:

```bash
cd phone
npm install
npx expo start        # Expo Go on the phone, same Wi-Fi
```

The whole app is `App.js`. It uses the same API as the web dashboard and authenticates with a
bearer token from `/api/auth/login`.
