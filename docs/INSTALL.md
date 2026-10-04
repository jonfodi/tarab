# Installing rasa (beta)

Apple Silicon Macs (M1 or newer), macOS 12+.

1. Open `rasa_0.1.0_aarch64.dmg` and drag **rasa** into **Applications**.
2. Open rasa. macOS will say it can't verify the app: rasa isn't signed with a paid Apple developer certificate
   yet. Click **Done** (not "Move to Trash").
3. Open **System Settings → Privacy & Security**, scroll down to *"rasa" was blocked…*, click **Open Anyway**, and
   confirm with your password. You only do this once.

   Terminal alternative: `xattr -dr com.apple.quarantine /Applications/rasa.app`

## First launch

- **Soulseek account:** rasa downloads from Soulseek, a peer-to-peer network. Log in with your Soulseek username and
  password, or invent new ones: the first login creates the account. If you also use SoulseekQt or Nicotine+ with
  the same account, quit them while rasa runs (an account can only be online once).
- **Music folder:** where verified tracks go, named `Artist - Title`. If you point it at an existing folder of
  `Artist - Title` files, use **Settings → Index existing music folder** so rasa doesn't download them again.
- **Old CDJs:** tick the MP3 option to also get 320k MP3 copies.
- **Sharing (recommended):** shares your rasa music folder with other Soulseek users. Many users serve sharers first
  and refuse people who share nothing, so this makes your downloads faster. Only files that passed rasa's checks are
  in that folder, and nobody can change or delete them. You can turn it off in Settings.
- **Router:** downloads work without any setup. If you want others to reach you directly (more sources, faster),
  forward TCP port 50300 on your router to your Mac.

## Using it

- **Get:** paste tracks, one per line: `Artist - Title`. Optional extras: `[CATALOG]` to prefer a specific release
  and `| m:ss` to pin an exact version, e.g. `Repair - Page-R [SUS006] | 6:51`. Tick "Title - Artist" if your list
  is the other way round. Or paste a Bandcamp album link to get a whole EP.
- **Listen:** tracks rasa couldn't confirm against official audio. Listen and click *Sounds right* or *Wrong track*
  (deletes it and looks again).
- **Wishlist:** tracks nobody is sharing in good quality right now. rasa re-checks every 20 minutes while it's open.

## Where things are

- Settings, database, slskd: `~/Library/Application Support/rasa/`
- Logs: `~/Library/Logs/rasa/engine.log`
- Soulseek password: macOS Keychain, entry `rasa-soulseek`

## Uninstall

Quit rasa, delete it from Applications, delete `~/Library/Application Support/rasa/` and `~/Library/Logs/rasa/`,
and remove the `rasa-soulseek` entry in Keychain Access. Your music folder is untouched.
