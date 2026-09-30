# Original C1000 app update capture

## Scope

This is offline inspection of the retained Android app, prompted by an original
C1000 offering main firmware 1.5.1 → 1.7.1. It does not download that update,
start an update, establish that the active UI uses every examined helper, or
change the phone. Raw app files, disassembly and capture material remain private.

The subsequent [isolated update-network trial](c1000-original-update-network.md)
records actual Wi-Fi/TLS failures, the official update and retained captures.
After a retry, the user confirmed installed **1.7.1** in the app. The offline
findings below describe candidate app paths, not proof of which path that
particular update used.

## Best identified capture route

The app's `WifiBleOtaLogic._execDownload` at `04073034` formats a log message
containing the update model, local file path and **download URL**, immediately
before calling `HttpRequestController.downloadOta`. That controller reaches
`DioForNative.download` in `02694384`.

Useful markers to search **privately** in a complete app logcat capture are:

```text
WifiBleLog
_execDownload()
_downloadOTABin()
otaPath ==
downloadOta
App.bin
```

Do not print matching lines without review: update-model logging can include
device serials and other identifiers. A captured URL is only a lead until its
host, path, query parameters and authentication requirements have been checked.
Preserve the original metadata locally; publish only a demonstrably public
vendor artifact with its provenance and hashes.

### Logging is conditional

`WifiBleLog.d` at `033838f8` is a real call to `Dev.exeInfo`, with a
`WifiBleLog` tag. The subsequent `Dev._exe` / `DevColorizedLog.internalCustom`
path has runtime enable/level gates and routes to a batcher, a Dart developer
log call and an optional configured callback. Static presence of a URL log
**does not prove that a production phone emits it to adb logcat**.

## Download location and lifetime

`WifiBleOtaLogic._createOTAFileUrl` at `04072de8` calls
`getApplicationDocumentsDirectory`, builds a device-derived subdirectory,
then appends the component and filename. Calls from `04072914` supply component
names `mcu` and `module`:

```text
<internal application documents>/<device-derived component>/mcu/App.bin
<internal application documents>/<device-derived component>/module/App.bin
```

The helper logs the directory and final path before creating the file. This is
an **internal app-documents API**, not an external-files API; an empty
`Android/data/com.anker.charging/files` does not rule out a downloaded image.
The base BLE OTA helper uses a similar internal `App.bin` path.

The generic OTA file manager at `0369b888` seeks and reads bytes from the
downloaded file to form chunks. Its examined read path does not perform a
separate firmware-content decryption. That observation does not establish
whether every vendor package is itself plaintext or signed/encrypted inside.

`WifiBleOtaLogic.onClose` invokes the base OTA close hook. The base hook at
`03383e6c` calls `_deleteOtaFile`, whose file deletion is at `03383f54`.
Preserve an accessible download before closing the update screen; do not stop
an update to obtain a file.

An optional read-only accessibility check is:

```sh
adb shell run-as com.anker.charging ls app_flutter
```

This works only if the installed app permits `run-as`; the directory name is
a candidate, while the logged path is authoritative. Ordinary production app
sandboxing may deny access. If it is denied and URL logging is suppressed,
this investigation has **not established a guaranteed non-root plaintext
download capture route**. It does not recommend repacking the app or modifying
its sandbox during an update.

## Original-model Bluetooth chunks are encrypted

Recovered class metadata links `A1761AnkerDevice` through `A1753AnkerDevice`.
The inherited `sendOtaSubPackage` implementation at `036bcfb4` calls the
shared chunk builder at `036be4a8`, then sends the result through the normal
`ZXCommandTransformer`:

- Snapshot opcode constant: `002f`.
- Function type: `0f`.
- Explicit `isEncrypted=true` argument.
- `useSecureInteraction` comes from the device's current setting.
- The normal generator and `DeviceController.sendCommand` follow.

The shared chunk builder writes the two-byte chunk index using `writeParam`,
then appends chunk bytes with `writeParamDIY`. This selected path is not a raw
DFU characteristic carrying an unencrypted firmware stream.

Two synthetic ARM64 replays of the shared final frame builder pass, selecting
CBC and GCM respectively. Both use fixed dummy ciphertext; they verify framing
and checksum, **not cryptography, key recovery, radio acceptance or a real
update**. The original wrapper's encryption argument is independently checked
in the instructions and named-argument metadata.

Thus a complete HCI capture remains valuable for handshake, command order,
chunk timing, acknowledgements and later protocol work, but encrypted chunks
alone should not be promised as a recoverable plaintext firmware image.

## Retention

Keep continuous full HCI and app logcat through download and completion, record
the offered/installed versions and component names, and save the resulting
bugreport privately. Do not clear the log buffer or restart the app during the
capture. Any accessible file should be copied and hashed without altering it.

Private reproduction material is under
`.solix-private/c1000-ota-app-20260930/`, including targeted AOT output,
`replay_ota_framing.py`, its synthetic result and a digest manifest. No raw app
data, actual OTA URLs, account identifiers or phone logs accompany this note.
