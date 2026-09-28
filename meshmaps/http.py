"""The one way the pipeline fetches a URL.

Named, because build-metadata.protomaps.dev answers Python's default User-Agent with a 403,
and a service's operator should be able to see who is asking.
"""

import urllib.request

USER_AGENT = "meshclient-maps/0.1 (+https://github.com/mcereal/meshclient-maps)"


def get(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()
