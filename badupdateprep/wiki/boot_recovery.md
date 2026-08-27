# Boot animation recovery (v1.3)

If you replaced bootanim.xex in flash with an unsigned file, Bad Update may no longer run. v1.3 can restore a clean boot animation.

1. Build USB with this app. Choose payload **Keep stock default.xex**.
2. FAT32 USB, Blitz trial or disc as usual.
3. On Rock Band Blitz “Press A To Start”: **hold Y, press A**, keep holding Y.
4. The exploit deletes unsigned/corrupt bootanim.xex and copies a clean one from BadUpdatePayload. You do not hunt for the file yourself.
5. Success: all four RoL quadrants green, then a 4-second countdown (one quadrant off per second), then reboot.
6. You should see the stock boot animation. After that, rebuild USB with XeUnshackle/FreeMyXe as default.xex and run Bad Update normally.
