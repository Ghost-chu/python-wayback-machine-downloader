import requests
import os
import gzip
import threading
import time
import json
import http.client
from urllib.parse import urljoin
from datetime import datetime, timezone

from pywaybackup.helper import url_get_timestamp, url_split, move_index, sanitize_filename

from pywaybackup.SnapshotCollection import SnapshotCollection as sc

from pywaybackup.Verbosity import Verbosity as v





# GET: store page to wayback machine and response with redirect to snapshot
# POST: store page to wayback machine and response with wayback machine status-page
# tag_jobid = '<script>spn.watchJob("spn2-%s", "/_static/",6000);</script>'
# tag_result_timeout = '<p>The same snapshot had been made %s minutes ago. You can make new capture of this URL after 1 hour.</p>'
# tag_result_success = ' A snapshot was captured. Visit page: <a href="%s">%s</a>'
def save_page(url: str):
    """
    Saves a webpage to the Wayback Machine. 

    Args:
        url (str): The URL of the webpage to be saved.

    Returns:
        None: The function does not return any value. It only prints messages to the console.
    """
    v.write("\nSaving page to the Wayback Machine...")
    connection = http.client.HTTPSConnection("web.archive.org")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }
    connection.request("GET", f"https://web.archive.org/save/{url}", headers=headers)
    v.write("\n-----> Request sent")
    response = connection.getresponse()
    response_status = response.status

    if response_status == 302:
        location = response.getheader("Location")
        v.write("\n-----> Response: 302 (redirect to snapshot)")
        snapshot_timestamp = datetime.strptime(url_get_timestamp(location), '%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
        current_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        timestamp_difference = (datetime.strptime(current_timestamp, '%Y-%m-%d %H:%M:%S') - datetime.strptime(snapshot_timestamp, '%Y-%m-%d %H:%M:%S')).seconds / 60
        timestamp_difference = int(round(timestamp_difference, 0))

        if timestamp_difference < 1:
            v.write("\n-----> New snapshot created")
        elif timestamp_difference > 1:
            v.write(f"\n-----> Snapshot already exists. (1 hour limit) - wait for {60 - timestamp_difference} minutes")
            v.write(f"TIMESTAMP SNAPSHOT: {snapshot_timestamp}")
            v.write(f"TIMESTAMP REQUEST : {current_timestamp}")
            v.write(f"\nLAST SNAPSHOT BACK: {timestamp_difference} minutes")

        v.write(f"\nURL: {location}")

    elif response_status == 404:
        v.write("\n-----> Response: 404 (not found)")
        v.write(f"\nFAILED -> URL: {url}")
    else:
        v.write("\n-----> Response: unexpected")
        v.write(f"\nFAILED -> URL: {url}")

    connection.close()




def print_list():
    v.write("")
    count = sc.count_list()
    if count == 0:
        v.write("\nNo snapshots found")
    else:
        __import__('pprint').pprint(sc.SNAPSHOT_COLLECTION)
        v.write(f"\n-----> {count} snapshots listed")





# create filelist
# timestamp format yyyyMMddhhmmss
def query_list(url: str, range: int, start: int, end: int, explicit: bool, mode: str, cdxbackup: str, cdxinject: str):
    if cdxinject:
        v.write("\nInjecting CDX data...")
        cdxResult = open(cdxinject, "r")
        cdxResult = cdxResult.read()
        v.write(f"\n-----> {sc.count_list()} snapshots injected")
    else:
        v.write("\nQuerying snapshots...")
        query_range = ""
        
        if not range:
            if start: query_range = query_range + f"&from={start}"
            if end: query_range = query_range + f"&to={end}"
        else: 
            query_range = "&from=" + str(datetime.now().year - range)

        # parse user input url and create according cdx url
        domain, subdir, filename = url_split(url)
        if domain and not subdir and not filename:
            cdx_url = f"*.{domain}/*" if not explicit else f"{domain}"
        if domain and subdir and not filename:
            cdx_url = f"{domain}/{subdir}/*"
        if domain and subdir and filename:
            cdx_url = f"{domain}/{subdir}/{filename}/*"
        if domain and not subdir and filename:
            cdx_url = f"{domain}/{filename}/*"

        v.write(f"---> {cdx_url}")
        cdxQuery = f"https://web.archive.org/cdx/search/cdx?output=json&url={cdx_url}{query_range}&fl=timestamp,digest,mimetype,statuscode,original&filter!=statuscode:200"

        try:
            cdxResult = requests.get(cdxQuery).text
        except requests.exceptions.ConnectionError as e:
            v.write(f"\n-----> ERROR: could not query snapshots:\n{e}")
            raise Exception("Error querying snapshots")
        
        if cdxbackup:
            os.makedirs(cdxbackup, exist_ok=True)
            with open(os.path.join(cdxbackup, f"waybackup_{sanitize_filename(url)}.cdx"), "w") as file: 
                file.write(cdxResult)
                v.write("\n-----> CDX backup generated")

    sc.create_list(json.loads(cdxResult), mode)
    v.write(f"\n-----> {sc.count_list()} snapshots found")






# example download: http://web.archive.org/web/20190815104545id_/https://www.google.com/
def download_list(output, retry, no_redirect, workers, skipset: set = None):
    """
    Download a list of urls in format: [{"timestamp": "20190815104545", "url": "https://www.google.com/"}]
    """
    if sc.count_list() == 0: 
        v.write("\nNothing to download");
        return
    v.write("\nDownloading snapshots...", progress=0)
    if workers > 1:
        v.write(f"\n-----> Simultaneous downloads: {workers}")
        batch_size = sc.count_list() // workers + 1
    else:
        batch_size = sc.count_list()
    sc.create_collection()
    v.write("\n-----> Snapshots prepared")
    batch_list = [sc.SNAPSHOT_COLLECTION[i:i + batch_size] for i in range(0, len(sc.SNAPSHOT_COLLECTION), batch_size)]    
    threads = []
    worker = 0
    for batch in batch_list:
        worker += 1
        v.write(f"\n-----> Starting worker: {worker}")
        thread = threading.Thread(target=download_loop, args=(batch, output, worker, retry, no_redirect, skipset))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()





def download_loop(snapshot_batch, output, worker, retry, no_redirect, skipset=None, attempt=1, connection=None):
    """
    Download a list of URLs in a recursive loop. If a download fails, the function will retry the download.
    The "snapshot_collection" dictionary will be updated with the download status and file information.
    Information for each entry is written by "create_entry" and "snapshot_dict_append" functions.
    """
    max_attempt = retry if retry > 0 else retry + 1
    failed_urls = []
    if not connection:
        connection = http.client.HTTPSConnection("web.archive.org")
    if attempt > max_attempt:
        connection.close()
        v.write(f"\n-----> Worker: {worker} - Failed downloads: {len(snapshot_batch)}")
        return
    for snapshot in snapshot_batch:
        status = f"\n-----> Attempt: [{attempt}/{max_attempt}] Snapshot [{snapshot_batch.index(snapshot)+1}/{len(snapshot_batch)}] - Worker: {worker}"
        download_status = download(output, snapshot, connection, status, no_redirect, skipset)
        if not download_status:
            failed_urls.append(snapshot)
        if download_status:
            v.write(progress=1)
    attempt += 1
    if failed_urls:
        if not attempt > max_attempt: 
            v.write(f"\n-----> Worker: {worker} - Retry Timeout: 15 seconds")
            time.sleep(15)
        download_loop(failed_urls, output, worker, retry, no_redirect, skipset, attempt, connection)            





def download(output, snapshot_entry, connection, status_message, no_redirect=False, skipset=None):
    """
    Download a single URL and save it to the specified filepath.
    If there is a redirect, the function will follow the redirect and update the download URL.
    gzip decompression is used if the response is encoded.
    According to the response status, the function will write a status message to the console and append a failed URL.
    """
    download_url = snapshot_entry["url_archive"]
    if skipset and skip_read(skipset, download_url):
        v.write(f"\nEXISTING -> URL: {download_url}")
        return True
    max_retries = 2
    sleep_time = 45
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'}
    for i in range(max_retries):
        try:
            connection.request("GET", download_url, headers=headers)
            response = connection.getresponse()
            response_data = response.read()
            response_status = response.status
            response_status_message = parse_response_code(response_status)
            sc.snapshot_entry_modify(snapshot_entry, "response", response_status)
            if not no_redirect:
                if response_status == 302:
                    status_message = f"{status_message}\n" + \
                        f"REDIRECT   -> HTTP: {response.status} - {response_status_message}\n" + \
                        f"           -> FROM: {download_url}"
                    redirect_count = 0
                    while response_status == 302 and redirect_count < 10:
                        redirect_count += 1
                        connection.request("GET", download_url, headers=headers)
                        response = connection.getresponse()
                        response_data = response.read()
                        response_status = response.status
                        response_status_message = parse_response_code(response_status)
                        location = response.getheader("Location")
                        if location:
                            download_url = urljoin(download_url, location)
                            status_message = f"{status_message}\n" + \
                                f"           ->   TO: {download_url}"
                            sc.snapshot_entry_modify(snapshot_entry, "redirect_timestamp", url_get_timestamp(location))
                            sc.snapshot_entry_modify(snapshot_entry, "redirect_url", download_url)
                        else:
                            break
            if response_status == 200:
                output_file = sc.create_output(download_url, snapshot_entry["timestamp"], output)
                output_path = os.path.dirname(output_file)

                # case if output_path is a file, move file to temporary name, create output_path and move file into output_path
                if os.path.isfile(output_path):
                    move_index(existpath=output_path)
                else: 
                    os.makedirs(output_path, exist_ok=True)
                # case if output_file is a directory, create file as index.html in this directory
                if os.path.isdir(output_file):
                    output_file = move_index(existfile=output_file)

                # if filename is too long, skip download
                if len(os.path.basename(output_file)) > 255:
                    status_message = f"{status_message}\n" + \
                        f"FILENAME TOO LONG -> HTTP: {response_status} - {response_status_message}\n" + \
                        f"                  -> URL: {download_url}"
                    v.write(status_message)
                    skip_write(skipset, snapshot_entry["url_archive"]) if skipset is not None else None
                    return True

                if not os.path.isfile(output_file):
                    with open(output_file, 'wb') as file:
                        if response.getheader('Content-Encoding') == 'gzip':
                            response_data = gzip.decompress(response_data)
                            file.write(response_data)
                        else:
                            file.write(response_data)
                    if os.path.isfile(output_file):
                        status_message = f"{status_message}\n" + \
                            f"SUCCESS    -> HTTP: {response_status} - {response_status_message}"
                else:
                    status_message = f"{status_message}\n" + \
                        f"EXISTING   -> HTTP: {response_status} - {response_status_message}"
                status_message = f"{status_message}\n" + \
                    f"           -> URL: {download_url}\n" + \
                    f"           -> FILE: {output_file}"
                v.write(status_message)
                sc.snapshot_entry_modify(snapshot_entry, "file", output_file)
                skip_write(skipset, snapshot_entry["url_archive"]) if skipset is not None else None
                return True
            
            else:
                status_message = f"{status_message}\n" + \
                    f"UNEXPECTED -> HTTP: {response_status} - {response_status_message}\n" + \
                    f"           -> URL: {download_url}"
                v.write(status_message)
                return True
        # exception returns false and appends the url to the failed list
        except http.client.HTTPException as e:
            status_message = f"{status_message}\n" + \
                f"EXCEPTION -> ({i+1}/{max_retries}), append to failed_urls: {download_url}\n" + \
                f"          -> {e}"
            v.write(status_message)
            return False
        # connection timeout waits and retries
        except requests.exceptions.Timeout as e:
            status_message = f"{status_message}\n" + \
                f"TIMEOUT   -> ({i+1}/{max_retries}), reconnect in {sleep_time} seconds...\n" + \
                f"         -> {e}"
            v.write(status_message)
            time.sleep(sleep_time)
        # connection refused waits and retries
        except ConnectionRefusedError as e:
            status_message = f"{status_message}\n" + \
                f"REFUSED  -> ({i+1}/{max_retries}), reconnect in {sleep_time} seconds...\n" + \
                f"         -> {e}"
            v.write(status_message)
            time.sleep(sleep_time)
    v.write(f"FAILED  -> download, append to failed_urls: {download_url}")
    return False

RESPONSE_CODE_DICT = {
    200: "OK",
    301: "Moved Permanently",
    302: "Found (redirect)",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
    503: "Service Unavailable"
}

def parse_response_code(response_code: int):
    """
    Parse the response code of the Wayback Machine and return a human-readable message.
    """
    if response_code in RESPONSE_CODE_DICT:
        return RESPONSE_CODE_DICT[response_code]
    return "Unknown response code"





def csv_close(csv_path: str, url: str):
    """
    Write a CSV file with the list of snapshots.
    """
    import csv
    url = sanitize_filename(url)
    if sc.count_list() > 0:
        v.write("\nSaving CSV-file...")
        os.makedirs(os.path.abspath(csv_path), exist_ok=True)
        with open(os.path.join(csv_path, f"waybackup_{url}.csv"), mode='w') as file:
            row = csv.DictWriter(file, sc.SNAPSHOT_COLLECTION[0].keys())
            row.writeheader()
            for snapshot in sc.SNAPSHOT_COLLECTION:
                row.writerow(snapshot)




def skip_open(skipset_path: str, url: str) -> tuple:
    """
    Opens an existing skip file or creates a new one.

    Args:
        skipset_path (str): The path to the skip-file.
        url (str): The URL of the webpage to be saved.

    Returns:
        tuple: A tuple containing the skip file object and the skip set.
    """
    domain, subdir, filename = url_split(url)
    default_file = f"waybackup_{domain}.skip"
    default_path = os.path.join(skipset_path, default_file)
    skipset = set()
    # check if custom or default skip file exists in the directory
    if os.path.isfile(skipset_path) or os.path.isfile(default_path):
        skipfile = open(skipset_path, mode='r+')
        skipset = set(skipfile.read().splitlines())
    else:
        skipfile = open(default_path, mode='w')

    return skipfile, skipset

def skip_write(skipset: set, archive_url: str):
    """
    Write a successed download to the set.
    """
    skipset.add(archive_url)

def skip_read(skipset: set, archive_url: str) -> bool:
    """
    Check if the URL is already downloaded and contained in the set.
    """
    return archive_url in skipset

def skip_close(skipfile: object, skipset: set):
    """
    Overwrite existing skip file with the new set content.
    """
    v.write("\nSaving skip-file...")
    skipfile.seek(0)
    skipfile.truncate()
    skipfile.write('\n'.join(skipset))
    skipfile.close()
    
    
    
