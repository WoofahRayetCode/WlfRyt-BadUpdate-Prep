# LED patterns and error codes

From the official How To Use wiki.

Slim consoles have **no orange** LEDs — patterns are green only. Meanings still apply; ignore color.

ASCII: O = orange, G = green, X = off.
Player 1 quadrant is always segment #1, whether the console is flat or standing.

Example (segment 1 green):

    G|X
    X|X

## Stage 1

    O|X
    X|X

Game-save exploit has ROP execution.

## Stage 2

    O|O
    X|X

Stage 2 started.

## Stage 3 (part 1)

    O|O
    O|X

Stage 3 started.

Diagonals (race won; often flashes many times):

    O|X    X|O
    X|O    O|X

Then:

    G|G
    G|X

Stage 3 finished; about to run stage 4 in hypervisor mode.

## Stage 4

    O|O
    O|O

Stage 4 payload running.

## Done

    G|G
    G|G

All green: hypervisor patched, default.xex runs.

## Error codes (stage 3)

Rare. Usually missing/corrupt USB files.

| Code | Meaning |
| 00, 01, 02 | Could not open/read update_data.bin in BadUpdatePayload |
| 03, 04, 05 | Could not open/read BadUpdateExploit-4thStage.bin |
| 06, 07, 08 | Failed to lock/thrash CPU L2 cache |
| 09, 10 | Failed to obtain ciphertext for malicious data |
| 11–15 | Out of memory allocating update buffers |
| 16 | Could not open/read xke_update.bin |
| 17 | Failed to create payload worker thread |
| 18 | Stage 4 payload failed to run |
