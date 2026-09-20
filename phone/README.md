# BURN-IN Remote (iPhone)

A native app that runs your BURN-IN desk from your phone: status, streams, clips, post-now,
pause, and the log. It talks to the BURN-IN app on your PC over your own Wi-Fi — nothing goes
through the internet and there is no account.

## Get it onto your phone (no Mac, no developer account)

Apple's build tools only run on macOS, so the `.ipa` is built on a free GitHub macOS runner and
signed on your PC by AltStore with your own Apple ID.

1. **Push this repo to GitHub** (public repo = free macOS runners).
2. **Actions → "Phone app (.ipa)" → Run workflow.** It takes ~15 minutes.
3. Download the **BURN-IN-Remote-ipa** artifact and unzip it — inside is `BURN-IN-Remote.ipa`.
4. **Install [AltStore](https://altstore.io) or [SideStore](https://sidestore.io)**: AltServer on
   Windows, the AltStore app on the phone, then sign in with your Apple ID.
5. In AltStore on the phone: **+ → BURN-IN-Remote.ipa**. It signs and installs it.
6. Keep AltServer running on the PC; it re-signs the app over Wi-Fi every 7 days (a free Apple ID
   limitation, not this app).

## Connect it

1. On the PC: **Settings → Dashboard → Phone remote**, then restart BURN-IN.
2. The Live desk shows a **Phone remote** button with the address, e.g. `192.168.0.72:8787`.
3. Open the app, type that address and your profile password. The session token is kept in the
   iOS keychain; the password is never stored.

## If it cannot connect

- Phone and PC must be on the same Wi-Fi (not guest or cellular).
- BURN-IN must be running with Phone remote on.
- Your PC's address changes if your router hands out a new one — retype it, or reserve the
  address in your router.
- Windows Firewall may ask to allow BURN-IN on private networks the first time. Allow it.

## Developing

```bash
cd phone
npm install
npx expo start        # Expo Go on the phone, same Wi-Fi
```

The whole app is `App.js`. It uses the same API as the web dashboard, authenticating with a
bearer token from `/api/auth/login`.
