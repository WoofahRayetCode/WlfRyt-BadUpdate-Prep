# How to use

Source: grimdoomer wiki “How To Use” (Emma, Mar 2025), plus v1.3 notes.

## Running the exploit

You need:

- Xbox 360 on dashboard 17559
- FAT32 USB
- Rock Band Blitz (arcade trial or full game)

### USB layout (this app builds it)

Root of the stick should contain:

- BadUpdatePayload/  (default.xex plus exploit bins)
- Content/            (hacked save + optional Blitz trial)
- name.txt            (if the pack included it)

default.xex must be retail-format with restrictions removed. XeUnshackle / FreeMyXe releases already are. XexTool if you swap your own:

    XexTool.exe -m r -r a <xex file>

### Disconnect from Xbox Live

Running the exploit while connected can **permanently ban** the console.

Unplug Ethernet. Forget saved Wi-Fi in Dashboard Network Settings.

### Load the hacked save

1. Plug the USB in, power on.
2. Rock Band Blitz has no bundled Player 1 profile. Use any **offline/local** profile, or stay signed out.
3. My Xbox → Games → Rock Band Blitz (USB trial) or your disc/HDD copy.
4. Title screen → press A. You should see “Running exploit…”. Music and menu should keep moving.

If it freezes, open the **Rock Band Blitz** page in this guide.

### Success rate

Older wiki text said ~30% and up to 20 minutes. **v1.3 is much faster** (often near-instant). If it sits too long with no LED progress, power off and retry.

When it finishes, all four ring-of-light segments go **green** and default.xex launches.

A numbered system error screen is rare; see **LEDs & errors**.
