> **Note:** this repo is a fork of the original github [project](https://github.com/nzbget/Completion)
> made by @hugbug.

## Requirements

- NZBGet v23+ and Python 3.8+
- Legacy NZBGet v22: use v2.0 release
- Python 3.7 or older: use v1.1.0 release
- Python 2.x: use v1.1.0 release

# Completion

[NZBGet](https://nzbget.com) [extension](https://github.com/nzbgetcom/nzbget/blob/main/docs/extensions/EXTENSIONS.md) that checks if the data in the NZB file is sufficiently complete at your usenet provider(s), before starting the download. If incomplete it would wait for a certain period and check the completion of the NZB file again. This check is done by requesting the header status, and is in normal cases done within seconds (like 1 - 5 sec. for a 1 GB file). This method is significantly faster than when NZBGet would report a failure, after actual downloading a (part of) the files that end up incomplete. The script is typically useful for issues related to:
- very recent posts,
- failed downloads, which after a while are just ok (propagation issues),
- incomplete posts,
- taken down posts (DMCA, etc.),
- old posts,
- long par repair times,
- downloading (parts of) NZB files beyond repair,
- unnecessary use of expensive block / slow fill accounts.

The above would generally result in error messages like ‘missing articles’, ‘unable to repair’ or ‘additional par files required’, ‘not enough par-blocs’, etc. The script avoids these messages.

Author: kloaknet
