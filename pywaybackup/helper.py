
from urllib.parse import urlparse
import os

def url_get_timestamp(url):
        """
        Extract the timestamp from a wayback machine URL.
        """
        timestamp = url.split("web.archive.org/web/")[1].split("id_/")[0]
        return timestamp

def url_split(url, index=False):
    """
    Split a URL into domain, subdir and filename.
    """
    # from pywaybackup.Verbosity import Verbosity as v
    if not urlparse(url).scheme:
        url = "http://" + url
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.split("@")[-1].split(":")[0] # split mailto: and port
    path_parts = parsed_url.path.split("/")
    if not url.endswith("/") or "." in path_parts[-1]:
        filename = path_parts[-1]
        subdir = "/".join(path_parts[:-1]).strip("/")
    else:
        filename = "index.html" if index else ""
        subdir = "/".join(path_parts).strip("/")
    filename = filename.replace("%20", " ") # replace url encoded spaces
    # v.write(f"URL-SPLIT: {url}")
    # v.write(f"Parsed URL: {parsed_url}")
    # v.write(f"DOMAIN: {domain}\nSUBDIR: {subdir}\nFILENAME: {filename}")
    return domain, subdir, filename
