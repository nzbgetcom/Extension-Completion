> **Note:** This script is compatible with python 3.8.x and above.
If you need support for Python 2.x versions then you can get legacy version v1.1.0 [here](https://forum.nzbget.net/viewtopic.php?f=8&t=1736&sid=c01b92bc3d3baf05bc1a9546d9c08ed8).

## NZBGet Versions

- pre-release v23+ [v3.0](https://github.com/nzbgetcom/Extension-Completion/releases/tag/v3.0)
- stable  v22 [v2.0](https://github.com/nzbgetcom/Extension-Completion/releases/tag/v2.0)
- legacy  v21 [v2.0](https://github.com/nzbgetcom/Extension-Completion/releases/tag/v2.0)

# Completion

[NZBGet](https://nzbget.com) [script](https://nzbget.com/documentation/post-processing-scripts/) that checks if the data in the NZB file is sufficiently complete at your usenet provider(s), before starting the download. If incomplete it would wait for a certain period and check the completion of the NZB file again. This check is done by requesting the header status, and is in normal cases done within seconds (like 1 - 5 sec. for a 1 GB file). This method is significantly faster than when NZBGet would report a failure, after actual downloading a (part of) the files that end up incomplete. The script is typically useful for issues related to:
- very recent posts,
- failed downloads, which after a while are just ok (propagation issues),
- incomplete posts,
- taken down posts (DMCA, etc.),
- old posts,
- long par repair times,
- downloading (parts of) NZB files beyond repair,
- unnecessary use of expensive block / slow fill accounts.

The above would generally result in an (error) messages like ‘missing articles’, ‘unable to repair’ or ‘additional par files required’, ‘not enough par-blocs’, etc. The script avoids these messages.

Author: kloaknet
