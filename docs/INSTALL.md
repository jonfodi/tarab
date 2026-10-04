# Installing tarab (beta)

Apple Silicon Macs (M1 or newer), macOS 12+.

1. Open `tarab_0.1.0_aarch64.dmg` and drag **tarab** into **Applications**.
2. Open tarab. macOS will say it can't verify the app: tarab isn't signed with a paid Apple developer certificate
   yet. Click **Done** (not "Move to Trash").
3. Open **System Settings → Privacy & Security**, scroll down to *"tarab" was blocked…*, click **Open Anyway**, and
   confirm with your password. You only do this once.

   Terminal alternative: `xattr -dr com.apple.quarantine /Applications/tarab.app`

## First launch

- **Soulseek account:** tarab downloads from Soulseek, a peer-to-peer network. Log in with your Soulseek username and
  password, or invent new ones: the first login creates the account. If you also use SoulseekQt or Nicotine+ with
  the same account, quit them while tarab runs (an account can only be online once).
- **Music folder:** where verified tracks go, named `Artist - Title`. If you point it at an existing folder of
  `Artist - Title` files, use **Settings → Index existing music folder** so tarab doesn't download them again.
- **Old CDJs:** tick the MP3 option to also get 320k MP3 copies.
- **Sharing (recommended):** shares your tarab music folder with other Soulseek users. Many users serve sharers first
  and refuse people who share nothing, so this makes your downloads faster. Only files that passed tarab's checks are
  in that folder, and nobody can change or delete them. You can turn it off in Settings.
- **Router:** downloads work without any setup. If you want others to reach you directly (more sources, faster),
  forward TCP port 50300 on your router to your Mac.

## Using it

- **Get:** paste tracks, one per line: `Artist - Title`. Optional extras: `[CATALOG]` to prefer a specific release
  and `| m:ss` to pin an exact version, e.g. `Repair - Page-R [SUS006] | 6:51`. Tick "Title - Artist" if your list
  is the other way round. Or paste a Bandcamp album link to get a whole EP.
- **Listen:** tracks tarab couldn't confirm against official audio. Listen and click *Sounds right* or *Wrong track*
  (deletes it and looks again).
- **Wishlist:** tracks nobody is sharing in good quality right now. tarab re-checks every 20 minutes while it's open.

## Where things are

- Settings, database, slskd: `~/Library/Application Support/tarab/`
- Logs: `~/Library/Logs/tarab/engine.log`
- Soulseek password: macOS Keychain, entry `tarab-soulseek`

## Uninstall

Quit tarab, delete it from Applications, delete `~/Library/Application Support/tarab/` and `~/Library/Logs/tarab/`,
and remove the `tarab-soulseek` entry in Keychain Access. Your music folder is untouched.
