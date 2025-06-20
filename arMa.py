import os
import sys
import subprocess
import argparse
import concurrent.futures
import time
from urllib.parse import urlparse, unquote, urljoin
import xml.etree.ElementTree as ET

import requests
import m3u8
from tqdm import tqdm

# Humanize import with fallback install
try:
    from humanize import naturalsize
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "humanize"])
    from humanize import naturalsize

AXM_BANNER = r"""
                                 /$$      /$$                
                                | $$$    /$$$                
  /$$$$$$         /$$$$$$       | $$$$  /$$$$        /$$$$$$ 
 |____  $$       /$$__  $$      | $$ $$/$$ $$       |____  $$
  /$$$$$$$      | $$  \__/      | $$  $$$| $$        /$$$$$$$
 /$$__  $$      | $$            | $$\  $ | $$       /$$__  $$
|  $$$$$$$      | $$            | $$ \/  | $$      |  $$$$$$$
 \_______/      |__/            |__/     |__/       \_______/
                                                             
                                                             
Advanced Downloader for M3U8, DASH, and Direct Files
@version 1.0 by Arnob
"""

HEADERS = {'User-Agent': 'Mozilla/5.0'}
MAX_RETRIES = 5
RETRY_BACKOFF = 2  # seconds

def get_filename_from_url(url):
    path = urlparse(url).path
    filename = os.path.basename(path)
    if '.' in filename:
        filename = filename.rsplit('.', 1)[0]
    return unquote(filename) or "output"

def ensure_unique_filename(directory, filename, extension):
    base_name = filename
    counter = 1
    candidate = os.path.join(directory, filename + extension)
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base_name}_{counter}{extension}")
        counter += 1
    return candidate

def retry_request(url, stream=False):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, stream=stream, timeout=15)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"[!] Attempt {attempt} failed for {url}: {e}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF ** attempt)

def download_file(url, directory, filename, resume=True):
    os.makedirs(directory, exist_ok=True)

    # Determine extension & full path
    head = requests.head(url, headers=HEADERS)
    content_type = head.headers.get('Content-Type', '')
    ext = os.path.splitext(urlparse(url).path)[1] or ''

    # Guess extension for common video types if missing
    if not ext and 'video' in content_type:
        if 'mp4' in content_type: ext = '.mp4'
        elif 'mpeg' in content_type: ext = '.mpeg'
        elif 'ts' in content_type: ext = '.ts'
        elif 'webm' in content_type: ext = '.webm'
        elif 'ogg' in content_type: ext = '.ogg'
    if not ext:
        ext = '.bin'

    full_path = ensure_unique_filename(directory, filename, ext)
    mode = 'ab' if resume and os.path.exists(full_path) else 'wb'
    downloaded = os.path.getsize(full_path) if mode == 'ab' else 0

    headers = HEADERS.copy()
    if mode == 'ab':
        headers['Range'] = f'bytes={downloaded}-'

    total_size = int(head.headers.get('Content-Length', 0)) + downloaded if 'Content-Length' in head.headers else None

    print(f"[+] Downloading file: {url}")

    with requests.get(url, headers=headers, stream=True) as r, \
         open(full_path, mode) as f, \
         tqdm(
            total=total_size, initial=downloaded,
            unit='B', unit_scale=True, unit_divisor=1024,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
            colour='cyan'
         ) as pbar:

        for chunk in r.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
                pbar.update(len(chunk))

    print(f"[✔] Saved file as: {full_path}")
    return full_path

def download_segment(url, directory, idx, total, retries=MAX_RETRIES):
    """
    Download a single segment with retry and resume support.
    """
    local_filename = os.path.join(directory, f"segment_{idx}.ts")
    temp_filename = local_filename + ".part"

    downloaded = os.path.getsize(temp_filename) if os.path.exists(temp_filename) else 0
    headers = HEADERS.copy()
    if downloaded > 0:
        headers['Range'] = f'bytes={downloaded}-'

    for attempt in range(1, retries+1):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=15) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('Content-Length', 0)) + downloaded if 'Content-Length' in r.headers else None
                with open(temp_filename, 'ab') as f, tqdm(
                    total=total_size, initial=downloaded,
                    unit='B', unit_scale=True, unit_divisor=1024,
                    desc=f"Segment {idx}/{total}",
                    leave=False,
                    colour='magenta'
                ) as pbar:
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk:
                            f.write(chunk)
                            pbar.update(len(chunk))
            os.rename(temp_filename, local_filename)
            return local_filename
        except Exception as e:
            print(f"[!] Segment {idx} download attempt {attempt} failed: {e}")
            time.sleep(RETRY_BACKOFF ** attempt)
    raise RuntimeError(f"Failed to download segment {idx} after {retries} retries.")

def download_and_merge_segments(playlist, directory, filename, parallel=4):
    os.makedirs(directory, exist_ok=True)
    segments = playlist.segments
    total = len(segments)

    print(f"[+] Starting parallel download of {total} segments with {parallel} workers")

    segment_files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {
            executor.submit(download_segment, seg.absolute_uri, directory, i+1, total): i+1
            for i, seg in enumerate(segments)
        }
        for future in tqdm(concurrent.futures.as_completed(futures), total=total, desc="Segments downloaded", colour='green'):
            idx = futures[future]
            try:
                segment_file = future.result()
                segment_files.append(segment_file)
            except Exception as e:
                print(f"[!] Segment {idx} failed to download: {e}")
                sys.exit(1)

    # Merge segments with ffmpeg
    concat_file = os.path.join(directory, f"{filename}_concat.txt")
    with open(concat_file, 'w') as f:
        for seg_file in sorted(segment_files):
            f.write(f"file '{os.path.abspath(seg_file)}'\n")

    mp4_file = ensure_unique_filename(directory, filename, ".mp4")
    print("[+] Merging segments into MP4...")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c", "copy", mp4_file
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[✔] Saved merged video as: {mp4_file}")

    # Cleanup segments and concat file
    for seg_file in segment_files:
        os.remove(seg_file)
    os.remove(concat_file)

def select_variant_playlist(master_playlist):
    """
    If master playlist, allow user to select variant by resolution or bandwidth.
    """
    variants = master_playlist.playlists
    if not variants:
        return master_playlist

    print("[+] Master playlist detected. Available qualities:")
    for i, variant in enumerate(variants, start=1):
        bw = variant.stream_info.bandwidth
        resolution = variant.stream_info.resolution
        res_str = f"{resolution[0]}x{resolution[1]}" if resolution else "Unknown"
        print(f"  {i}. Bandwidth: {bw} bps, Resolution: {res_str}")

    choice = input(f"Select quality (1-{len(variants)}), or press Enter for highest quality: ")
    try:
        choice_idx = int(choice) - 1
        if 0 <= choice_idx < len(variants):
            selected_uri = variants[choice_idx].absolute_uri
            print(f"[+] Selected quality #{choice}: {selected_uri}")
            return m3u8.load(selected_uri)
    except Exception:
        pass

    # Default highest bandwidth variant
    highest = max(variants, key=lambda v: v.stream_info.bandwidth or 0)
    print(f"[+] Defaulting to highest quality: {highest.absolute_uri}")
    return m3u8.load(highest.absolute_uri)

def download_subtitles(playlist, directory):
    """
    Download available subtitles from EXT-X-MEDIA tags with TYPE=SUBTITLES
    """
    os.makedirs(directory, exist_ok=True)
    subtitles = [media for media in playlist.media if media.type == 'SUBTITLES']
    if not subtitles:
        print("[+] No subtitles found.")
        return

    print(f"[+] Found {len(subtitles)} subtitle track(s). Downloading...")

    for i, sub in enumerate(subtitles, 1):
        uri = sub.uri
        lang = sub.language or f"sub{i}"
        filename = ensure_unique_filename(directory, f"subtitle_{lang}", os.path.splitext(uri)[1] or ".vtt")
        try:
            print(f"  - Downloading subtitle '{lang}' from {uri}")
            r = retry_request(uri)
            with open(filename, 'wb') as f:
                f.write(r.content)
            print(f"    Saved as {filename}")
        except Exception as e:
            print(f"    Failed to download subtitle '{lang}': {e}")

def is_mpd_url(url):
    """
    Simple check for DASH MPD manifest by extension or content-type
    """
    if url.lower().endswith('.mpd'):
        return True
    try:
        r = requests.head(url, timeout=5)
        ctype = r.headers.get('Content-Type', '')
        if 'application/dash+xml' in ctype:
            return True
    except:
        pass
    return False

def parse_dash_mpd(url):
    """
    Parse DASH MPD and extract segment URLs to download sequentially.
    For simplicity, only supports SegmentTemplate with media URLs.
    """
    print(f"[+] Parsing DASH MPD manifest: {url}")
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()

    root = ET.fromstring(r.content)
    ns = {'mpd': 'urn:mpeg:dash:schema:mpd:2011'}

    # Find base URL
    base_url = url.rsplit('/', 1)[0] + '/'

    # Find Period->AdaptationSet->Representation with SegmentTemplate
    segments = []
    for period in root.findall('mpd:Period', ns):
        for adap_set in period.findall('mpd:AdaptationSet', ns):
            for rep in adap_set.findall('mpd:Representation', ns):
                seg_tmpl = rep.find('mpd:SegmentTemplate', ns)
                if seg_tmpl is not None:
                    media = seg_tmpl.get('media')
                    initialization = seg_tmpl.get('initialization')
                    timescale = int(seg_tmpl.get('timescale', '1'))
                    start_number = int(seg_tmpl.get('startNumber', '1'))
                    duration = int(seg_tmpl.get('duration', '0'))

                    # Download initialization segment
                    if initialization:
                        init_url = urljoin(base_url, initialization.replace('$RepresentationID$', rep.get('id')))
                        segments.append(init_url)

                    # Calculate total segments count roughly (for demo assume 10)
                    count = 10

                    # Download media segments
                    for i in range(start_number, start_number + count):
                        media_url = media.replace('$RepresentationID$', rep.get('id')).replace('$Number$', str(i))
                        segments.append(urljoin(base_url, media_url))
                    break

    return segments

def download_dash(url, directory, filename):
    os.makedirs(directory, exist_ok=True)
    segments = parse_dash_mpd(url)
    total = len(segments)

    print(f"[+] Downloading {total} DASH segments")

    segment_files = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(download_segment, seg_url, directory, idx + 1, total): idx + 1
            for idx, seg_url in enumerate(segments)
        }
        for future in tqdm(concurrent.futures.as_completed(futures), total=total, desc="DASH segments", colour='blue'):
            idx = futures[future]
            try:
                segment_file = future.result()
                segment_files.append(segment_file)
            except Exception as e:
                print(f"[!] DASH segment {idx} failed: {e}")
                sys.exit(1)

    # Merge DASH segments (assuming ts segments)
    concat_file = os.path.join(directory, f"{filename}_concat.txt")
    with open(concat_file, 'w') as f:
        for seg_file in sorted(segment_files):
            f.write(f"file '{os.path.abspath(seg_file)}'\n")

    mp4_file = ensure_unique_filename(directory, filename, ".mp4")
    print("[+] Merging DASH segments into MP4...")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c", "copy", mp4_file
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[✔] Saved merged DASH video as: {mp4_file}")

    # Cleanup
    for seg_file in segment_files:
        os.remove(seg_file)
    os.remove(concat_file)


def is_m3u8_url(url):
    """Quick check if URL points to m3u8 playlist by checking extension or content."""
    # Check extension first
    if url.lower().endswith('.m3u8'):
        return True
    # If extension unknown, try to check content-type header
    try:
        r = requests.head(url, timeout=5)
        content_type = r.headers.get('Content-Type', '')
        if 'application/vnd.apple.mpegurl' in content_type or 'application/x-mpegURL' in content_type:
            return True
    except:
        pass
    return False

def main():
    print(AXM_BANNER)

    parser = argparse.ArgumentParser(
        description="Advanced downloader for M3U8, DASH, and direct files with resume, parallelism, retries, and subtitles."
    )
    parser.add_argument("--url", required=True, help="The media URL to download (M3U8, DASH MPD, or direct file).")
    parser.add_argument("--name", default=None, help="Optional output filename (without extension).")
    parser.add_argument("--dir", default="downloads", help="Directory to save downloads (default: downloads).")
    parser.add_argument("--parallel", type=int, default=4, help="Number of parallel downloads for segments (default: 4).")

    args = parser.parse_args()

    if is_mpd_url(args.url):
        filename = args.name if args.name else get_filename_from_url(args.url)
        download_dash(args.url, args.dir, filename)
    elif is_m3u8_url(args.url):
        playlist = m3u8.load(args.url)
        if playlist.is_variant:
            playlist = select_variant_playlist(playlist)
        filename = args.name if args.name else get_filename_from_url(args.url)

        download_subtitles(playlist, args.dir)
        download_and_merge_segments(playlist, args.dir, filename, parallel=args.parallel)
    else:
        filename = args.name if args.name else get_filename_from_url(args.url)
        download_file(args.url, args.dir, filename)


if __name__ == "__main__":
    main()
