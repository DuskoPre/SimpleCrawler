#!/usr/bin/env python3
"""
Markdown Crawler - A multithreaded web crawler that recursively crawls a website
and creates a markdown file for each page.

Usage:
    python SimpleCrawler.py https://example.com
    python SimpleCrawler.py https://example.com --max-depth 5 --num-threads 10
"""

import argparse
import logging
import os
import queue
import re
import threading
import time
import urllib.parse
from typing import List, Optional, Union

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

__version__ = ''
__author__ = ''
__copyright__ = ""

BANNER = """
SimpleCrawler
"""

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_BASE_DIR = 'markdown'
DEFAULT_MAX_DEPTH = 3
DEFAULT_NUM_THREADS = 5
DEFAULT_TARGET_CONTENT = ['article', 'div', 'main', 'p']
DEFAULT_TARGET_LINKS = ['body']
DEFAULT_DOMAIN_MATCH = True
DEFAULT_BASE_PATH_MATCH = True


# --------------
# URL validation
# --------------
def is_valid_url(url: str) -> bool:
    """Check if a URL is valid."""
    try:
        result = urllib.parse.urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        logger.debug(f'❌ Invalid URL {url}')
        return False


# ----------------
# Clean up the URL
# ----------------
def normalize_url(url: str) -> str:
    """Normalize a URL by removing fragments and query parameters."""
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), None, None, None))


# ------------------
# HTML parsing logic
# ------------------
def crawl(
    url: str,
    base_url: str,
    already_crawled: set,
    file_path: str,
    target_links: Union[str, List[str]] = DEFAULT_TARGET_LINKS,
    target_content: Union[str, List[str]] = None,
    valid_paths: Union[str, List[str]] = None,
    is_domain_match: Optional[bool] = DEFAULT_DOMAIN_MATCH,
    is_base_path_match: Optional[bool] = DEFAULT_BASE_PATH_MATCH,
    is_links: Optional[bool] = False
) -> List[str]:
    """Crawl a single URL and extract content and child links."""
    if url in already_crawled:
        return []
    
    try:
        logger.debug(f'Crawling: {url}')
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f'❌ Request error for {url}: {e}')
        return []
    
    if 'text/html' not in response.headers.get('Content-Type', ''):
        logger.error(f'❌ Content not text/html for {url}')
        return []
    
    already_crawled.add(url)

    # ---------------------------------
    # List of elements we want to strip
    # ---------------------------------
    strip_elements = ['a'] if is_links else []

    # -------------------------------
    # Create BS4 instance for parsing
    # -------------------------------
    soup = BeautifulSoup(response.text, 'html.parser')

    # Strip unwanted tags
    for script in soup(['script', 'style', 'nav', 'header', 'footer']):
        script.decompose()

    # --------------------------------------------
    # Write the markdown file if it does not exist
    # --------------------------------------------
    if not os.path.exists(file_path):
        file_name = file_path.split("/")[-1]

        # ------------------
        # Get target content
        # ------------------
        content = get_target_content(soup, target_content=target_content)

        if content:
            # --------------
            # Parse markdown
            # --------------
            output = md(
                content,
                keep_inline_images_in=['td', 'th', 'a', 'figure'],
                strip=strip_elements
            )

            # Clean up extra whitespace
            output = re.sub(r'\n{3,}', '\n\n', output)
            
            # Add URL as metadata
            output = f"# Source: {url}\n\n{output}"

            logger.info(f'Created 📝 {file_name}')

            # ------------------------------
            # Write markdown content to file
            # ------------------------------
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(output)
        else:
            logger.warning(f'⚠️ Empty content for {file_path}')

    child_urls = get_target_links(
        soup,
        base_url,
        target_links,
        valid_paths=valid_paths,
        is_domain_match=is_domain_match,
        is_base_path_match=is_base_path_match    
    )

    logger.debug(f'Found {len(child_urls) if child_urls else 0} child URLs')
    return child_urls


def get_target_content(
    soup: BeautifulSoup,
    target_content: Union[List[str], None] = None
) -> str:
    """Extract the main content from the page using specified selectors."""
    content = ''
    main_content = None

    # -------------------------------------
    # Get target content by target selector
    # -------------------------------------
    if target_content:
        for target in target_content:
            for tag in soup.select(target):
                content += str(tag).replace('\n', '')
        if content:
            return content

    # ---------------------------
    # Naive estimation of content
    # ---------------------------
    max_text_length = 0
    for tag in soup.find_all(DEFAULT_TARGET_CONTENT):
        text_length = len(tag.get_text(strip=True))
        if text_length > max_text_length:
            max_text_length = text_length
            main_content = tag

    return str(main_content) if main_content else ''


def get_target_links(
    soup: BeautifulSoup,
    base_url: str,
    target_links: List[str] = DEFAULT_TARGET_LINKS,
    valid_paths: Union[List[str], None] = None,
    is_domain_match: Optional[bool] = DEFAULT_DOMAIN_MATCH,
    is_base_path_match: Optional[bool] = DEFAULT_BASE_PATH_MATCH
) -> List[str]:
    """Extract links from the page that match the crawling criteria."""
    child_urls = []

    # Get all urls from target_links
    for target in soup.find_all(target_links):
        # Get all the links in target
        for link in target.find_all('a', href=True):
            href = link.get('href')
            if href and not href.startswith('#') and not href.startswith('javascript:'):
                child_urls.append(urllib.parse.urljoin(base_url, href))

    result = []
    base_parsed = urllib.parse.urlparse(base_url)
    
    for u in child_urls:
        child_parsed = urllib.parse.urlparse(u)

        # ---------------------------------
        # Check if domain match is required
        # ---------------------------------
        if is_domain_match and child_parsed.netloc != base_parsed.netloc:
            continue

        # Check if path is valid
        if is_base_path_match and child_parsed.path.startswith(base_parsed.path):
            result.append(u)
            continue

        if valid_paths:
            for valid_path in valid_paths:
                if child_parsed.path.startswith(urllib.parse.urlparse(valid_path).path):
                    result.append(u)
                    break

    return result


# ------------------
# Worker thread logic
# ------------------
def worker(
    q: queue.Queue,
    base_url: str,
    max_depth: int,
    already_crawled: set,
    base_dir: str,
    target_links: Union[List[str], None] = DEFAULT_TARGET_LINKS,
    target_content: Union[List[str], None] = None,
    valid_paths: Union[List[str], None] = None,
    is_domain_match: bool = None,
    is_base_path_match: bool = None,
    is_links: Optional[bool] = False
) -> None:
    """Worker thread that processes URLs from the queue."""
    while not q.empty():
        try:
            depth, url = q.get(timeout=1)
        except queue.Empty:
            break
            
        if depth > max_depth:
            continue
            
        # Create a safe filename from the URL path
        path = urllib.parse.urlparse(url).path
        file_name = re.sub(r'[^a-zA-Z0-9-]', '-', path.strip('/')) if path.strip('/') else 'index'
        file_name = re.sub(r'-+', '-', file_name)  # Remove consecutive dashes
        file_path = os.path.join(base_dir, f'{file_name}.md')

        child_urls = crawl(
            url,
            base_url,
            already_crawled,
            file_path,
            target_links,
            target_content,
            valid_paths,
            is_domain_match,
            is_base_path_match,
            is_links
        )
        
        child_urls = [normalize_url(u) for u in child_urls]
        for child_url in child_urls:
            q.put((depth + 1, child_url))
            
        time.sleep(0.5)  # Be polite to the server


# -----------------
# Thread management
# -----------------
def md_crawl(
    base_url: str,
    max_depth: Optional[int] = DEFAULT_MAX_DEPTH,
    num_threads: Optional[int] = DEFAULT_NUM_THREADS,
    base_dir: Optional[str] = DEFAULT_BASE_DIR,
    target_links: Union[str, List[str]] = DEFAULT_TARGET_LINKS,
    target_content: Union[str, List[str]] = None,
    valid_paths: Union[str, List[str]] = None,
    is_domain_match: Optional[bool] = None,
    is_base_path_match: Optional[bool] = None,
    is_debug: Optional[bool] = False,
    is_links: Optional[bool] = False
) -> None:
    """Main function to crawl a website and convert to markdown."""
    if is_domain_match is False and is_base_path_match is True:
        raise ValueError('❌ Domain match must be True if base match is set to True')

    is_domain_match = DEFAULT_DOMAIN_MATCH if is_domain_match is None else is_domain_match
    is_base_path_match = DEFAULT_BASE_PATH_MATCH if is_base_path_match is None else is_base_path_match

    if not base_url:
        raise ValueError('❌ Base URL is required')

    # Convert string parameters to lists
    if isinstance(target_links, str):
        target_links = target_links.split(',') if ',' in target_links else [target_links]

    if isinstance(target_content, str):
        target_content = target_content.split(',') if ',' in target_content else [target_content]

    if isinstance(valid_paths, str):
        valid_paths = valid_paths.split(',') if ',' in valid_paths else [valid_paths]

    # Configure logging
    if is_debug:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
        logger.debug('🐞 Debugging enabled')
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    logger.info(f'🕸️ Crawling {base_url} at ⏬ depth {max_depth} with 🧵 {num_threads} threads')

    # Validate the base URL
    if not is_valid_url(base_url):
        raise ValueError('❌ Invalid base URL')

    # Create base_dir if it doesn't exist
    os.makedirs(base_dir, exist_ok=True)

    already_crawled = set()

    # Create a queue of URLs to crawl
    q = queue.Queue()
    q.put((0, base_url))

    threads = []

    # Create threads
    for i in range(num_threads):
        t = threading.Thread(
            target=worker,
            args=(
                q,
                base_url,
                max_depth,
                already_crawled,
                base_dir,
                target_links,
                target_content,
                valid_paths,
                is_domain_match,
                is_base_path_match,
                is_links
            ),
            daemon=True
        )
        threads.append(t)
        t.start()
        logger.debug(f'Started thread {i+1} of {num_threads}')

    # Wait for all threads to finish
    for t in threads:
        t.join()

    logger.info(f'🏁 All threads have finished. Crawled {len(already_crawled)} pages.')


# -----------------
# Command line interface
# -----------------
def main():
    """Command line interface for the markdown crawler."""
    print(BANNER)
    
    parser = argparse.ArgumentParser(
        description='Crawl a website and convert pages to markdown files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s https://example.com
  %(prog)s https://example.com -d 5 -t 10
  %(prog)s https://example.com -c "article,main" -l "body"
  %(prog)s https://example.com -v "/docs,/tutorials"
        '''
    )
    
    parser.add_argument('base_url', type=str, help='Base URL to crawl (ex. https://example.com)')
    
    parser.add_argument(
        '--max-depth', '-d', 
        required=False, default=DEFAULT_MAX_DEPTH, type=int, 
        help=f'Max depth of child links to crawl (default: {DEFAULT_MAX_DEPTH})'
    )
    
    parser.add_argument(
        '--num-threads', '-t', 
        required=False, default=DEFAULT_NUM_THREADS, type=int, 
        help=f'Number of threads to use for crawling (default: {DEFAULT_NUM_THREADS})'
    )
    
    parser.add_argument(
        '--base-dir', '-b', 
        required=False, default=DEFAULT_BASE_DIR, type=str, 
        help=f'Base directory to save markdown files in (default: {DEFAULT_BASE_DIR})'
    )
    
    parser.add_argument(
        '--debug', '-e', 
        action='store_true', default=False, 
        help='Enable debug mode'
    )
    
    parser.add_argument(
        '--target-content', '-c', 
        required=False, type=str, default=','.join(DEFAULT_TARGET_CONTENT), 
        help=f'CSS target path of the content to extract from each page (default: {",".join(DEFAULT_TARGET_CONTENT)})'
    )
    
    parser.add_argument(
        '--target-links', '-l', 
        required=False, type=str, default=','.join(DEFAULT_TARGET_LINKS), 
        help=f'CSS target path containing the links to crawl (default: {",".join(DEFAULT_TARGET_LINKS)})'
    )
    
    parser.add_argument(
        '--valid-paths', '-v', 
        required=False, type=str, default=None, 
        help='Comma separated list of valid relative paths to crawl (ex. /wiki,/categories,/help)'
    )
    
    parser.add_argument(
        '--domain-match', '-m', 
        action='store_true', default=DEFAULT_DOMAIN_MATCH, 
        help=f'Crawl only links that match the base domain (default: {DEFAULT_DOMAIN_MATCH})'
    )
    
    parser.add_argument(
        '--base-path-match', '-p', 
        action='store_true', default=DEFAULT_BASE_PATH_MATCH, 
        help=f'Crawl only links that match the base path of the base_url specified in CLI (default: {DEFAULT_BASE_PATH_MATCH})'
    )
    
    parser.add_argument(
        '--links', '-i', 
        action='store_true', default=True, 
        help='Enable the conversion of links in the markdown output (default: True)'
    )
    
    # Parse arguments
    args = parser.parse_args()

    # Convert string parameters to lists
    target_content = args.target_content.split(',') if args.target_content else None
    target_links = args.target_links.split(',') if args.target_links else None
    valid_paths = args.valid_paths.split(',') if args.valid_paths else None

    # Run the crawler
    try:
        md_crawl(
            args.base_url,
            max_depth=args.max_depth,
            num_threads=args.num_threads,
            base_dir=args.base_dir,
            target_content=target_content,
            target_links=target_links,
            valid_paths=valid_paths,
            is_domain_match=args.domain_match,
            is_base_path_match=args.base_path_match,
            is_debug=args.debug,
            is_links=args.links
        )
    except KeyboardInterrupt:
        print('\n⏹️ Crawling interrupted by user')
    except Exception as e:
        logger.error(f'❌ Error: {e}')
        return 1
    
    return 0


# --------------
# CLI entrypoint
# --------------
if __name__ == '__main__':
    exit(main())
