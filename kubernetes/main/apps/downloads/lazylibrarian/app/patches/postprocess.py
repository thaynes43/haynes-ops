# ---------------------------------------------------------------------------
# haynes-ops override (downloads/lazylibrarian, ConfigMap lazylibrarian-postprocess)
#
# This is LazyLibrarian's upstream lazylibrarian/postprocess.py, byte for byte,
# plus this comment block and ONE marked change (search for "haynes-ops fix").
#
# PINNED TO UPSTREAM: image docker.io/linuxserver/lazylibrarian:version-40a389ea
#   (LazyLibrarian commit 40a389ea, pyproject version 2026.05.25).
#   sha256 of the unmodified upstream file in that image:
#   8b60d3a2a11ba6c98cd3abca16dd76207efdf68256206bbbc4ef77fa01275c26
#
# The fix: when SABnzbd fails a job (for example "Duplicate NZB", when one
# release is grabbed for two books in one search run), get_download_progress()
# sets the wanted row to Aborted and returns progress -1. _get_ready_from_snatched()
# took -1 as ready to process (it means "torrent removed after seeding"), so
# the fuzzy second pass imported the other book's same-named download as this
# book (thaynes43/haynesnetwork#688). A SABnzbd row that the progress check has
# just aborted is now skipped, and the status pass fails it and puts the book
# back to Wanted. A torrent's -1 is processed as before.
#
# BEFORE BUMPING THE IMAGE TAG in helmrelease.yaml: take the new image's
# postprocess.py, and either re-apply the marked change to it (if upstream has
# not fixed the bug) or drop this override (if it has). Never carry this file
# onto a different image unchanged: it would silently revert every other
# upstream change to the post-processor.
# ---------------------------------------------------------------------------
#  This file is part of Lazylibrarian.
#
#  Lazylibrarian is free software, you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  Lazylibrarian is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with Lazylibrarian.  If not, see <http://www.gnu.org/licenses/>.

import contextlib
import datetime
import json
import logging
import os
import re
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from rapidfuzz import fuzz

import lazylibrarian
from lazylibrarian import database
from lazylibrarian.archive_utils import unpack_archive, unpack_multipart
from lazylibrarian.bookrename import audio_rename, name_vars, stripspaces
from lazylibrarian.cache import ImageType, cache_img
from lazylibrarian.calibre_integration import send_to_calibre
from lazylibrarian.common import multibook, run_script
from lazylibrarian.config2 import CONFIG
from lazylibrarian.download_client import (
    check_contents,
    delete_task,
    get_download_folder,
    get_download_name,
    get_download_progress,
)
from lazylibrarian.filesystem import (
    DIRS,
    book_file,
    bts_file,
    copy_tree,
    get_directory,
    jpg_file,
    listdir,
    make_dirs,
    path_exists,
    path_isdir,
    path_isfile,
    remove_file,
    safe_copy,
    safe_move,
    setperm,
    syspath,
)
from lazylibrarian.formatter import (
    check_int,
    get_list,
    is_valid_type,
    make_unicode,
    md5_utf8,
    now,
    plural,
    sanitize,
    thread_name,
    today,
    unaccented,
)
from lazylibrarian.images import create_mag_cover, createthumbs
from lazylibrarian.importer import update_totals
from lazylibrarian.magazinescan import create_id
from lazylibrarian.mailinglist import mailing_list
from lazylibrarian.metadata_opf import create_comic_opf, create_mag_opf, create_opf
from lazylibrarian.notifiers import (
    custom_notify_download,
    custom_notify_snatch,
    notify_download,
    notify_snatch,
)
from lazylibrarian.postprocess_metadata import (
    BookMetadata,
    BookType,
    ComicMetadata,
    EbookMetadata,
    MagazineMetadata,
    prepare_book_metadata,
    prepare_comic_metadata,
    prepare_magazine_metadata,
)
from lazylibrarian.postprocess_utils import enforce_bytes, enforce_str
from lazylibrarian.preprocessor import (
    preprocess_audio,
    preprocess_ebook,
    preprocess_magazine,
)
from lazylibrarian.scheduling import SchedulerCommand, schedule_job
from lazylibrarian.telemetry import TELEMETRY


@dataclass
class BookState:
    """
    Represents the state of a book/magazine being processed.

    This class tracks the download item as it moves through the postprocessing
    pipeline, maintaining information about the candidate file/folder location,
    metadata, and processing status.

    Attributes:
        book_id: Unique identifier for the book/magazine
        book_title: Normalized title for matching
        aux_type: Type of media from aux_info field (eBook, AudioBook, Magazine)
        aux_info: Auxiliary information (e.g., issue date for magazines)
        completed_at: Unix timestamp when download completed
        mode_type: Download mode (torrent, magnet, torznab, nzb, etc.)

        candidate_ptr: Current path to the candidate file/folder being evaluated.
                      This pointer gets updated as we drill down through folders
                      and extract archives to find the actual media file.

        skipped_reason: Reason why processing was skipped (if applicable)
        copy_to_destination: Whether to copy (vs move) files to destination
    """

    # Core identifiers (from database row)
    book_id: str
    download_title: str  # NZBtitle (download/torrent name) - for location finding
    book_title: str = ""  # Actual book title from books table - for drill-down matching
    aux_type: str = (
        ""  # case sensitive AuxInfo book type (eBook, AudioBook, Magazine, comic)
    )
    aux_info: str = ""

    # Download metadata
    completed_at: int = 0
    mode_type: str = ""
    source: str = ""  # Download client source (SABnzbd, Deluge, etc.)
    download_id: str = ""  # ID in download client for tracking/deletion
    download_folder: str = ""  # Folder path from download client (for targeted search)
    download_url: str = ""  # URL of the download (unique identifier)
    download_provider: str = ""  # Provider name (for stats/notifications)
    status: str = "Snatched"  # Current status (Snatched, Seeding, Aborted, etc.)
    snatched_date: str = ""  # When snatched (for timeout calculations)

    # Processing state - mutable as we search for matches
    candidate_ptr: str | None = None
    skipped_reason: str | None = None

    # Failure tracking - populated during processing
    failure_reason: str = ""
    processing_stage: str = (
        ""  # "matching", "validation", "metadata", "destination", "post"
    )
    was_processed: bool = False  # True if successfully processed

    # Configuration flags
    copy_to_destination: bool = False

    # Runtime state (for unprocessed download tracking)
    aborted: bool = False
    finished: bool = False
    progress: int | str = "Unknown"

    @classmethod
    def from_db_row(cls, book_row: dict, config) -> "BookState":
        """
        Create a BookState instance from a database row.

        Args:
            book_row: Database row from 'wanted' table
            config: Configuration object

        Returns:
            Initialized BookState instance
        """
        book_data = dict(book_row)
        normalized_download_title = _normalize_title(book_data["NZBtitle"])
        mode_type = book_data["NZBmode"]

        return cls(
            book_id=book_data["BookID"],
            download_title=normalized_download_title,
            book_title="",  # Will be populated later if needed for drill-down
            aux_type=_extract_aux_type(book_data),
            aux_info=book_data["AuxInfo"],
            completed_at=check_int(book_data["Completed"], 0),
            mode_type=mode_type,
            source=book_data.get("Source", ""),
            download_id=book_data.get("DownloadID", ""),
            download_folder="",  # Will be populated in _get_ready_snatched_books
            download_url=book_data.get("NZBurl", ""),
            download_provider=book_data.get("NZBprov", ""),
            status=book_data.get("Status", "Snatched"),
            snatched_date=book_data.get("NZBdate", ""),
            copy_to_destination=config.get_bool("DESTINATION_COPY"),
            candidate_ptr=None,
            skipped_reason=None,
        )

    def is_completed(self) -> bool:
        """Check if the download has a completion timestamp."""
        return self.completed_at > 0

    def seconds_since_completion(self) -> int:
        """Calculate seconds since download completed (rounded up)."""
        if not self.is_completed():
            return 0
        completion = time.time() - self.completed_at
        return int(-(-completion // 1))  # Round up to int

    def should_delay_processing(self, delay_seconds: int) -> "tuple[bool, int]":
        """
        Check if processing should be delayed based on completion time.

        Args:
            delay_seconds: Required delay in seconds from config (PP_DELAY)

        Returns:
            Tuple of (should_delay, seconds_since_completion)
        """
        if not self.is_completed():
            return False, 0

        seconds_elapsed = self.seconds_since_completion()
        should_delay = seconds_elapsed < delay_seconds

        return should_delay, seconds_elapsed

    def update_candidate(self, new_path: str) -> None:
        """
        Update the candidate pointer to a new location.

        This is called as we drill down through directories and extract archives
        to find the actual media file.

        Args:
            new_path: New path to set as candidate
        """
        self.candidate_ptr = new_path

    def mark_skipped(self, reason: str) -> None:
        """
        Mark this item as skipped with a reason.

        Args:
            reason: Human-readable reason for skipping
        """
        self.skipped_reason = reason

    def is_skipped(self) -> bool:
        """Check if this item was marked as skipped."""
        return self.skipped_reason is not None

    def is_torrent(self) -> bool:
        """Convenience flag indicating if this is a torrent download"""
        return self.mode_type in ["torrent", "magnet", "torznab"]

    def has_candidate(self) -> bool:
        """Check if we have found a candidate file/folder."""
        return self.candidate_ptr is not None

    def is_book(self) -> bool:
        """Check if this is a book (ebook or audiobook)."""
        book_type_enum = self.get_book_type_enum()
        return book_type_enum in [BookType.EBOOK, BookType.AUDIOBOOK]

    def is_magazine(self) -> bool:
        """Check if this is a magazine."""
        book_type_enum = self.get_book_type_enum()
        return book_type_enum in [BookType.MAGAZINE]

    def get_book_type_str(self) -> str:
        """
        Get the book type as a string

        Returns:
            gets normalized string for book type based on aux info type
        """
        book_type_str = ""
        try:
            book_type_str = self.get_book_type_enum().value
        except ValueError:
            contextlib.suppress(ValueError)

        return book_type_str

    def get_book_type_enum(self) -> BookType:
        """
        Get the book type as a string

        Returns:
            gets normalized string for book type based on aux info type
        Raises ValueError if aux_type is invalid book type
        """
        return BookType.from_string(self.aux_type)

    def has_download_client(self) -> bool:
        """
        Check if this download has a source and download ID.

        Returns:
            True if both source and download_id are set
        """
        return bool(self.source and self.download_id)

    def can_delete_from_client(self) -> bool:
        """
        Check if we can delete this from the download client.

        Returns:
            True if download can be deleted from client
        """
        if not self.source:
            return False
        if not self.download_id or self.download_id == "unknown":
            return False
        return self.source != "DIRECT"

    def get_display_name(self, config) -> str:
        """
        Get formatted provider name for notifications.

        Args:
            config: Configuration object

        Returns:
            Formatted display name with optional title/URL
        """
        dispname = config.disp_name(self.download_provider)

        if config.get_bool("NOTIFY_WITH_TITLE"):
            dispname = f"{dispname}: {self.book_title}"

        if config.get_bool("NOTIFY_WITH_URL"):
            dispname = f"{dispname}: {self.download_url}"

        return dispname

    def is_seeding(self) -> bool:
        """Check if status indicates seeding."""
        return self.status == "Seeding"

    def is_snatched(self) -> bool:
        """Check if status indicates snatched."""
        return self.status == "Snatched"

    def is_aborted(self) -> bool:
        """Check if status indicates aborted."""
        return self.status == "Aborted"

    def mark_failed(self, stage: str, reason: str) -> None:
        """
        Mark this item as failed with stage and reason.

        Args:
            stage: Processing stage where failure occurred
                   ("matching", "validation", "metadata", "destination", "post")
            reason: Human-readable reason for failure
        """
        self.processing_stage = stage
        self.failure_reason = reason

    def mark_processed(self) -> None:
        """Mark this item as successfully processed"""
        self.was_processed = True

    def has_failed(self) -> bool:
        """Check if this item failed during processing"""
        return bool(self.failure_reason)

    def enrich_with_download_info(self, db) -> None:
        """
        Populate download_folder and book_title from download client and database.

        This is called after BookState creation to add:
        - download_folder: Exact folder path from download client (for targeted search)
        - book_title: Actual book title from books table (for drill-down matching)

        Args:
            db: Database connection for querying book title
        """
        # Get specific download folder by combining general folder + download name
        logger = logging.getLogger(__name__)

        if self.source and self.download_id:
            general_folder = get_download_folder(self.source, self.download_id)
            res = None
            if self.source == 'DIRECT':
                res = db.match("SELECT NZBprov,NZBtitle,NZBurl from wanted where source='DIRECT' and DownloadID=?", (self.download_id, ))
                download_name = res['NZBtitle']
            else:
                download_name = get_download_name(self.download_title, self.source, self.download_id)
            # For usenet clients (SABnzbd, NZBGet), the storage field already contains
            # the complete download path including the folder name
            if self.source in ("SABNZBD", "NZBGET") and general_folder:
                if os.path.isdir(general_folder):
                    self.download_folder = general_folder
                else:
                    self.download_folder = os.path.dirname(general_folder)
            elif res and res['NZBprov'] == 'soulseek':
                try:
                    soulseek = json.loads(res['NZBurl'].split('^')[1].replace('\\','/'))
                    self.download_folder = os.path.join(get_directory('Download'), soulseek.get("name", '').rsplit('/', 1)[1])
                except (KeyError, IndexError):
                    self.download_folder = general_folder
            elif res and (res['NZBprov'] in ['annas', 'zlibrary'] or res['NZBprov'].startswith('libgen')):
                # these download into first download directory
                self.download_folder = get_directory('Download')
            elif self.source == "QBITTORRENT" and general_folder:
                if os.path.isdir(general_folder):
                    # root directory for multi-file torrents
                    self.download_folder = general_folder
                else:
                    # absolute file path for single-file torrents
                    self.download_folder = os.path.dirname(general_folder)
                    self.download_title = os.path.basename(general_folder)
            # For other torrent clients, combine base folder with download name
            elif general_folder and download_name:
                self.download_folder = os.path.join(general_folder, download_name)
            elif general_folder:
                # Fallback: use general folder as-is
                self.download_folder = general_folder

            logger.debug(f"General:{general_folder} DownloadName:{download_name} "
                          f"DownloadFolder:{self.download_folder} DownloadTitle:{self.download_title}")
        # Get actual book title for drill-down matching
        if self.book_id and self.is_book():
            result = db.match(
                "SELECT AuthorName, BookName FROM books,authors "
                "WHERE books.BookID=? AND books.AuthorID=authors.AuthorID",
                (self.book_id,),
            )
            if result:
                data = dict(result)
                # Combine author and book name for better matching
                self.book_title = _normalize_title(
                    f"{data.get('AuthorName', '')} - {data.get('BookName', '')}"
                )
        elif self.book_id and self.is_magazine():
            result = db.match(
                "SELECT Title FROM magazines WHERE Title=?", (self.book_id,)
            )
            if result:
                data = dict(result)
                self.book_title = _normalize_title(data.get("Title", ""))
        logger.debug(f"IsBook:{self.is_book()} IsMag:{self.is_magazine()} Title:{self.book_title}")

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"BookState(id={self.book_id}, type={self.aux_type}, "
            f"download_title='{self.download_title[:30]}...', source={self.source}, "
            f"status={self.status}, candidate={self.candidate_ptr}, "
            f"skipped={self.is_skipped()})"
        )


def PostProcessor():  # was cron_process_dir
    """Scheduled postprocessor entry point.

    Thread safety is handled inside process_dir() itself.
    """
    logger = logging.getLogger(__name__)
    if lazylibrarian.STOPTHREADS:
        logger.debug("STOPTHREADS is set, not starting postprocessor")
        schedule_job(SchedulerCommand.STOP, target="PostProcessor")
    else:
        # process_dir() has its own thread safety and thread naming
        process_dir()


def process_img(
    dest_path: str,
    bookid,
    bookimg,
    global_name,
    cache: ImageType = ImageType.BOOK,
    overwrite=False,
):
    """cache the bookimg from url or filename, and optionally copy it to bookdir"""
    # if lazylibrarian.CONFIG['IMP_AUTOADD_BOOKONLY']:
    #     logger.debug('Not creating coverfile, bookonly is set')
    #     return

    logger = logging.getLogger(__name__)
    coverfile = jpg_file(dest_path)
    if not overwrite and coverfile:
        logger.debug(f"Cover {coverfile} already exists")
        return
    if not bookimg:
        logger.debug(f"No cover to cache for {bookid}")
        return

    TELEMETRY.record_usage_data("Process/Image")
    if bookimg.startswith("cache/"):
        img = bookimg.replace("cache/", "")
        if os.path.__name__ == "ntpath":
            img = img.replace("/", "\\")
        cachefile = os.path.join(DIRS.CACHEDIR, img)
    else:
        link, success, _ = cache_img(cache, bookid, bookimg, False)
        if not success:
            logger.error(f"Error caching cover from {bookimg}, {link}")
            return
        cachefile = os.path.join(DIRS.DATADIR, link)

    try:
        coverfile = os.path.join(dest_path, f"{global_name}.jpg")
        coverfile = safe_copy(cachefile, coverfile)
        setperm(coverfile)
    except Exception as e:
        logger.error(
            f"Error copying image {bookimg} to {coverfile}, {type(e).__name__} {e!s}"
        )
        return


def _update_downloads_provider_count(provider: str = "manually added"):
    """
    Count the number of times that each provider was used for each download.

    Args:
        provider: Optional name of provider of download
    """
    db = database.DBConnection()
    entry = dict(db.match("SELECT Count FROM downloads where Provider=?", (provider,)))
    if entry:
        counter = int(entry["Count"])
        db.action(
            "UPDATE downloads SET Count=? WHERE Provider=?", (counter + 1, provider)
        )
    else:
        db.action(
            "INSERT into downloads (Count, Provider) VALUES  (?, ?)", (1, provider)
        )
    db.close()


def _transfer_matching_files(
    sourcedir: str, targetdir: str, fname_prefix: str, copy=False
):
    """
    Selectively transfer files matching a filename prefix from source to target directory.

    Only transfers files that:
    - Start with the given filename prefix
    - Are valid media file types

    This prevents accidentally moving/copying unrelated files when processing
    a single file in a directory with multiple downloads.

    Args:
        sourcedir: Source directory containing files
        targetdir: Target directory to transfer to
        fname_prefix: Filename prefix to match (e.g., "Book Title")
        copy: If True, copy files; if False, move files

    Returns:
        Count of files transferred
    """
    logger = logging.getLogger(__name__)
    cnt = 0
    list_dir = listdir(sourcedir)
    valid_extensions = CONFIG.get_all_types_list()
    logger.debug(f"Source: {sourcedir}")
    logger.debug(f"Contents {list_dir}")
    logger.debug(f"Valid extensions {valid_extensions}")
    logger.debug(f"Destination: {targetdir}")
    for _ourfile in list_dir:
        ourfile = str(_ourfile)
        logger.debug(f"Checking if {ourfile} matches {fname_prefix}")
        # Only transfer files that start with our book's name and are valid media files
        if ourfile.startswith(fname_prefix) and is_valid_type(ourfile, extensions=valid_extensions):
            try:
                srcfile = os.path.join(sourcedir, ourfile)
                dstfile = os.path.join(targetdir, ourfile)
                logger.debug(f"Trying to {'copy' if copy else 'move'} {srcfile} to {dstfile}")

                if copy:
                    dstfile = safe_copy(srcfile, dstfile)
                    setperm(dstfile)
                    logger.debug(f"Copied {ourfile} to subdirectory")
                else:
                    dstfile = safe_move(srcfile, dstfile)
                    setperm(dstfile)
                    logger.debug(f"Moved {ourfile} to subdirectory")
                cnt += 1
            except Exception as why:
                logger.warning(
                    f"Failed to transfer file {ourfile} to [{targetdir}], "
                    f"{type(why).__name__} {why!s}"
                )
                continue
    if copy:
        logger.debug(f"Copied {cnt} to subdirectory")
    else:
        logger.debug(f"Moved {cnt} to subdirectory")
    return cnt


def _extract_aux_type(book: dict) -> str:
    """
    A simple helper function to ensure the value is valid possibility for the AuxInfo column
    """
    book_type = book["AuxInfo"]
    if book_type not in ["AudioBook", "eBook", "comic"]:
        book_type = "eBook" if not book_type else "Magazine"
    return book_type


def _update_download_status(
    book_state: BookState, db, logger: logging.Logger, dlresult=None
):
    """
    Update the status of a completed download based on its current state.

    Args:
        book_state: The book/download record
        db: Database connection
        logger: Logger instance
        dlresult: Optional result message for the download

    Returns:
        str: The new status ('Seeding', 'Processed', or None if not updated)
    """
    if not isinstance(book_state.progress, int) or book_state.progress != 100:
        return None

    # Determine if this should be marked as Seeding
    is_torrent = book_state.mode_type in ["torrent", "magnet", "torznab"]
    should_keep_seeding = (
        is_torrent and CONFIG.get_bool("KEEP_SEEDING") and not book_state.finished
    )

    if should_keep_seeding:
        # Mark as Seeding - download complete but still active in client
        cmd = "UPDATE wanted SET Status='Seeding' WHERE NZBurl=? and Status IN ('Snatched', 'Processed')"
        db.action(cmd, (book_state.download_url,))
        logger.info(
            f"STATUS: {book_state.download_title} [{book_state.status} -> Seeding] "
            f"Download complete, continuing to seed"
        )
        return "Seeding"
    # Mark as Processed - download complete
    if not dlresult:
        dlresult = "Download complete"
    cmd = "UPDATE wanted SET Status='Processed', DLResult=? WHERE NZBurl=? and Status='Snatched'"
    db.action(cmd, (dlresult, book_state.download_url))
    logger.info(
        f"STATUS: {book_state.download_title} [{book_state.status} -> Processed] {dlresult}"
    )

    # Optionally delete from client if configured
    if book_state.finished and CONFIG.get_bool("DEL_COMPLETED"):
        logger.debug(
            f"Deleting {book_state.download_title} from {book_state.source} (DEL_COMPLETED=True)"
        )
        delete_task(book_state.source, book_state.download_id, False)
    return "Processed"


def _get_ready_from_snatched(db, snatched_list: list[dict]):
    """
    Filter snatched books to find those ready for processing.

    Filters out books that:
    - Have rejected content (file size, banned extensions, etc.)
    - Are still downloading (progress 0-99%)
    - Have incomplete torrents

    Updates download names if they changed in the client (common with torrents).

    Args:
        db: Database connection
        snatched_list: List of books with Status='Snatched'

    Returns:
        List of book rows ready to process (download complete and content valid)
    """
    logger = logging.getLogger(__name__)

    books_to_process = []  # the filtered list of books ready for processing
    delete_failed = CONFIG.get_bool("DEL_FAILED")

    for book_row in snatched_list:
        # Get current status from the downloader as the name may have changed
        # once magnet resolved, or download started or completed.
        # This is common with torrent downloaders. Usenet doesn't change the name.
        book_id = book_row["BookID"]
        book_type_str = BookType.from_string(
            _extract_aux_type(book_row)
        ).value  # normalize aux info type
        title = unaccented(book_row["NZBtitle"], only_ascii=False)
        source = book_row["Source"]
        download_id = book_row["DownloadID"]
        download_url = book_row["NZBurl"]
        download_name = get_download_name(title, source, download_id)

        if download_name and download_name != title:
            if source == "SABNZBD":
                logger.warning(
                    f"{source} unexpected change [{title}] to [{download_name}]"
                )
            logger.debug(f"{source} Changing [{title}] to [{download_name}]")
            # should we check against reject word list again as the name has changed?
            db.action(
                "UPDATE wanted SET NZBtitle=? WHERE NZBurl=?",
                (download_name, download_url),
            )
            title = download_name

        rejected = check_contents(source, download_id, book_type_str, title)
        if rejected:
            logger.debug(f"Rejected: {title} BookID: {book_id} DownloadID: {download_id}")

            # change status to "Failed", and ask downloader to delete task and files
            # Only reset book status to wanted if still snatched in case another download task succeeded
            if book_id != "unknown":
                cmd = ""
                if book_type_str == BookType.EBOOK.value:
                    cmd = "UPDATE books SET status='Wanted' WHERE status='Snatched' and BookID=?"
                elif book_type_str == BookType.AUDIOBOOK.value:
                    cmd = "UPDATE books SET audiostatus='Wanted' WHERE audiostatus='Snatched' and BookID=?"
                if cmd:
                    db.action(cmd, (book_id,))
                # use downloadid as identifier as bookid is not unique for magazine issues
                db.action(
                    "UPDATE wanted SET Status='Failed',DLResult=? WHERE DownloadID=?",
                    (rejected, download_id),
                )
                logger.info(
                    f"STATUS: {title} [Snatched -> Failed] Content rejected: {rejected}"
                )
                if delete_failed:
                    delete_task(source, download_id, True)
            continue

        # Check if download is complete before processing download directories
        progress, finished = get_download_progress(source, download_id)
        # haynes-ops fix: SABnzbd returns -1 only for a failed history slot (e.g. "Duplicate NZB"), and
        # get_download_progress has just set the row to Aborted. Never process it: the fuzzy second pass
        # would import another book's same-named download (thaynes43/haynesnetwork#688). The status pass
        # fails the row and puts the book back to Wanted. A torrent's -1 (removed after seeding) still runs.
        if progress == -1 and source == "SABNZBD":
            row = db.match("SELECT Status, DLResult FROM wanted WHERE DownloadID=? and Source=?",
                           (download_id, source))
            if row and row["Status"] == "Aborted":
                logger.info(f"{title} failed at {source} ({row['DLResult']}), not processing")
                continue
        # progress can be: -1 (not found/removed - may be seeding complete), 0-99 (in progress), 100+ (complete)
        # finished is True only when downloader confirms completion
        # Process if: progress >= 100 (complete/seeding), finished == True, or progress == -1
        # (torrent removed after seeding)
        # Skip only if: 0 <= progress < 100 and not finished (actively downloading)
        if 0 <= progress < 100 and not finished:
            logger.debug(
                f"Download not yet complete for {title} (progress: {progress}%), skipping"
            )
            continue

        # If we reach this point, this book can be processed
        logger.debug(
            f"Download for '{title}' of type {book_type_str} ready to process."
        )
        books_to_process.append(book_row)

    return books_to_process


def _normalize_title(title: str) -> str:
    # remove accents and convert not-ascii apostrophes
    new_title = str(unaccented(title, only_ascii=False))
    # torrent names might have words_separated_by_underscores
    new_title = new_title.split(" LL.(")[0].replace("_", " ")
    year_len: Final[int] = 4
    # Strip known file extensions and special suffixes from the end
    # This handles cases like "Book Name.2013" or "folder.unpack" or "Book.epub"
    # but preserves periods in names like "J.R.R. Tolkien" or "Dr. Seuss"
    if '.' in new_title:
        # Get the part after the last period
        last_dot_index = new_title.rfind(".")
        suffix = new_title[last_dot_index + 1:].lower()

        # Strip if it matches known patterns:
        # 1. Known file extensions
        known_extensions = CONFIG.get_all_types_list() if CONFIG else []
        # 2. Special suffixes
        special_suffixes = ['unpack']
        # 3. 4-digit years
        is_year = suffix.isdigit() and len(suffix) == year_len

        if suffix in known_extensions or suffix in special_suffixes or is_year:
            new_title = new_title[:last_dot_index]

    # strip noise characters
    return sanitize(new_title).strip()


def _tokenize_file(filepath_or_name: str) -> "tuple[str, str]":
    """
    Extract filename stem and extension from a file path.

    Example:
        >>> _tokenize_file("/path/to/file.epub")
        ("file", "epub")

    Args:
        filepath_or_name: Full path or filename

    Returns:
        Tuple of (stem, extension) where extension has no leading dot
    """
    path_obj = Path(filepath_or_name)
    stem = path_obj.stem
    # Slice off the leading dot from the suffix
    extension = path_obj.suffix[1:]
    return stem, extension


def _is_valid_media_file(
    filepath: str, book_type=BookType.EBOOK.value, include_archives=False
) -> bool:
    """
    Check if a file is a valid media type for processing.

    Args:
        filepath: Path to the file to check
        book_type: Type of media ("ebook", "audiobook", "magazine", "comic")
        include_archives: Whether to include comic book archives (cbr, cbz)

    Returns:
        True if file is a valid media type
    """
    if include_archives:
        return is_valid_type(
            filepath, extensions=CONFIG.get_all_types_list(), extras="cbr, cbz"
        )
    return CONFIG.is_valid_booktype(filepath, booktype=book_type)


def _count_zipfiles_in_directory(directory_path: str) -> int:
    """
    Count zip files in a directory, excluding epub and cbz files.

    Args:
        directory_path: Path to directory to scan

    Returns:
        Number of zip files found (excluding ebook/comic formats)
    """
    zipcount = 0
    for _f in listdir(directory_path):
        f = enforce_str(_f)  # Ensure string for path operations
        file_path = os.path.join(directory_path, f)
        _, extn = _tokenize_file(f)
        extn = extn.lower()

        # Skip ebook and comic book formats that happen to be zips
        if extn not in [".epub", ".cbz"] and zipfile.is_zipfile(file_path):
            zipcount += 1
    return zipcount


def _find_valid_file_in_directory(
    directory_path, book_type=BookType.EBOOK.value, recurse=False
) -> str:
    """
    Find the first valid media file in a directory.

    Args:
        directory_path: Path to directory to search
        book_type: Type of media to look for
        recurse: Whether to search subdirectories

    Returns:
        Path to first valid file found, or empty string if none found
    """
    if recurse:
        for _dirpath, _, files in os.walk(directory_path):
            dirpath = enforce_str(_dirpath)  # Ensure string for path operations
            for _item in files:
                item = enforce_str(_item)  # Ensure string for path operations
                if _is_valid_media_file(
                    item, book_type=book_type, include_archives=True
                ):
                    return os.path.join(dirpath, item)
    else:
        for _f in listdir(directory_path):
            f = enforce_str(_f)  # Ensure string for path operations
            if _is_valid_media_file(f, book_type=book_type, include_archives=True):
                return os.path.join(directory_path, f)
    return ""


def _extract_best_match_from_collection(
    candidate_dir,
    target_title: str,
    download_dir: str,
    logger: logging.Logger,
    fuzzlogger: logging.Logger,
) -> "tuple[str, bool, str]":
    """
    Extract the best matching book from a multi-book collection.

    When a download contains multiple books, find the one that best matches
    the target title and copy it to an isolated directory for processing.

    Args:
        candidate_dir: Directory containing multiple books
        target_title: The title we're trying to match
        download_dir: Parent download directory for creating .unpack folder
        logger: Logger instance
        fuzzlogger: Fuzzy matching logger instance

    Returns:
        Tuple of (extracted_path, skipped, skip_reason)
        - extracted_path: Path to the extracted book directory
        - skipped: Whether extraction was skipped
        - skip_reason: Reason for skipping, if applicable
    """
    match_threshold = CONFIG.get_int("DLOAD_RATIO")
    best_match = None
    best_score = 0

    # Find the best matching file
    for _f in listdir(candidate_dir):
        f = enforce_str(_f)  # Ensure string for validation
        if CONFIG.is_valid_booktype(f, booktype=BookType.EBOOK.value):
            filename_stem, _ = _tokenize_file(f)
            normalized_fname = _normalize_title(filename_stem)

            match_percent = fuzz.token_set_ratio(target_title, normalized_fname)
            is_match = match_percent >= match_threshold
            fuzzlogger.debug(
                f"{round(match_percent, 2)}% match {target_title} : {normalized_fname}"
            )

            if is_match and match_percent > best_score:
                best_match = f
                best_score = match_percent

    if not best_match:
        return "", True, "Multiple books found with no good match"

    # Create isolated directory for the best match
    # use md5 to handle enormously long titles overflowing windows directory name limit
    target_dir = os.path.join(download_dir, f"{md5_utf8(target_title)[-8:]}.unpack")
    if not make_dirs(target_dir, new=True):
        logger.error(f"Failed to create target dir {target_dir}")
        return "", True, "Failed to create extraction directory"

    logger.debug(
        f"Best candidate match: {best_match} ({round(best_score, 2)}%) "
        f"for {target_title} in multi book collection"
    )

    best_match_stem, _ = _tokenize_file(best_match)

    # Copy all files related to the best match (including .opf, .jpg)
    for _f in listdir(candidate_dir):
        f = enforce_str(_f)  # Ensure string for validation and path operations
        filename_stem, _ = _tokenize_file(f)

        if filename_stem == best_match_stem and (CONFIG.is_valid_booktype(
            f, booktype=BookType.EBOOK.value
        ) or _is_metadata_file(f)):
            source = os.path.join(candidate_dir, f)
            if path_isdir(source):
                shutil.copytree(source, target_dir)
            else:
                dest = os.path.join(target_dir, f)
                shutil.copyfile(source, dest)

    return target_dir, False, ""


def _validate_candidate_directory(
    candidate_ptr, logger: logging.Logger
) -> "tuple[bool, str]":
    """
    Validate that a candidate directory is suitable for processing.

    Checks for:
    - Empty directories
    - Presence of .bts files (BitTorrent Sync files)

    Args:
        candidate_ptr: Path to candidate directory
        logger: Logger instance

    Returns:
        Tuple of (is_valid, skip_reason)
    """
    if not listdir(candidate_ptr):
        logger.debug(f"Skipping {candidate_ptr}, folder is empty")
        return False, "Folder is empty"

    if bts_file(candidate_ptr):
        logger.debug(f"Skipping {candidate_ptr}, found a .bts file")
        return False, "Folder contains .bts file"

    return True, ""


def _extract_archives_in_directory(
    candidate_ptr,
    download_dir: str,
    title: str,
) -> "tuple[str, bool]":
    """
    Extract all archives in a directory and return the path to extracted content.

    Handles both multipart archives and regular archives. Updates the candidate
    pointer to the extracted location if successful.

    Args:
        candidate_ptr: Directory containing archives
        download_dir: Parent download directory
        title: Title for naming extracted content

    Returns:
        Tuple of (new_candidate_ptr, content_changed)
        - new_candidate_ptr: Updated path (same as input if nothing changed)
        - content_changed: Whether any archives were extracted
    """
    # Count zip files to detect multipart archives
    zipfile_count = _count_zipfiles_in_directory(candidate_ptr)

    if zipfile_count == 0:
        return candidate_ptr, False

    incoming_candidate = candidate_ptr

    # Handle multipart archives first
    if zipfile_count > 1:
        unpacked_path = unpack_multipart(candidate_ptr, download_dir, title)
        if unpacked_path:
            candidate_ptr = unpacked_path

    # Extract remaining archives
    for _dirpath, _, files in os.walk(candidate_ptr):
        dirpath = enforce_str(_dirpath)  # Ensure string for path operations
        for item in files:
            _, extn = _tokenize_file(item)
            extn = extn.lower()

            # Skip files that are ebooks/comics (they're already in zip format)
            if item and extn not in [".epub", ".cbr", ".cbz"]:
                if not os.path.exists(os.path.join(dirpath, item)):
                    continue
                res = unpack_archive(os.path.join(dirpath, item), download_dir, title)
                if res:
                    candidate_ptr = res
                    break

    content_changed = candidate_ptr != incoming_candidate
    return candidate_ptr, content_changed


def _calculate_fuzzy_match(title1: str, title2: str, fuzzlogger: logging.Logger=None) -> float:
    """
    Calculate fuzzy match percentage between two titles.

    Args:
        title1: First title to compare
        title2: Second title to compare

    Returns:
        Match percentage (0-100)
    """
    match_fuzz = fuzz.token_set_ratio(title1, title2)

    if match_fuzz >= CONFIG.get_int('NAME_RATIO'):
        # if it's a close enough match, check for purely number differences
        # This is in case we have multiple issues of a magazine in the folder
        # where the only difference is the date
        # magazine issue No 1.pdf magazine issue No 2.pdf etc.  where the fuzzy match is too close
        num1 = []
        num2 = []
        set1 = set(title1.split())
        set2 = set(title2.split())
        for word in set1:
            # see if word coerces to an integer or a float
            word = word.replace('-', '')
            try:
                num1.append(float(re.findall(r'\d+\.\d+', word)[0]))
            except IndexError:
                with contextlib.suppress(IndexError):
                    num1.append(int(re.findall(r'\d+', word)[0]))
        for word in set2:
            word = word.replace('-', '')
            try:
                num2.append(float(re.findall(r'\d+\.\d+', word)[0]))
            except IndexError:
                with contextlib.suppress(IndexError):
                    num2.append(int(re.findall(r'\d+', word)[0]))
        if fuzzlogger:
            fuzzlogger.debug(f"[{title1}][{title2}]{num1}:{num2}")
        if num1 and num2 and num1 != num2:
            return CONFIG.get_int('NAME_RATIO') - 1  # only difference is a number, not the same
    return match_fuzz


def _find_matching_subdir(
    directory: str,
    target_title: str,
    match_threshold: float,
    book_type: str,
    logger: logging.Logger,
) -> "tuple[str, float]":
    """
    Search a directory for a SUBDIRECTORY that matches the target title and contains books.

    Used for collections organized with each book in its own subdirectory.
    Particularly common for audiobook series downloads.

    Args:
        directory: Parent directory to search
        target_title: Normalized title to match against
        match_threshold: Minimum match percentage to consider valid
        book_type: Type of media to look for
        logger: Logger for reporting matching debug messages

    Returns:
        Tuple of (matched_subdir_path, match_percent)
        Returns ("", 0) if no matching subdirectory found
    """
    best_match_path = ""
    best_match_percent = 0

    try:
        items = listdir(directory)
    except Exception as e:
        logger.debug(f"Error listing directory {directory}: {e}")
        return "", 0

    for _item in items:
        item = enforce_str(_item)  # Ensure string for path operations
        item_path = os.path.join(directory, item)

        # Only consider subdirectories
        if path_isdir(item_path):
            try:
                # Check if this subdirectory contains the target book type
                subdir_files = listdir(item_path)
                has_book = any(
                    CONFIG.is_valid_booktype(enforce_str(f), booktype=book_type)
                    for f in subdir_files
                )

                if has_book:
                    # Fuzzy match the subdirectory name against target
                    # _normalize_title now handles stripping known extensions intelligently
                    normalized_dirname = _normalize_title(item)
                    match_percent = _calculate_fuzzy_match(
                        target_title, normalized_dirname, logger
                    )

                    logger.debug(
                        f"{round(match_percent, 2)}% match (subdir) {target_title} : {normalized_dirname}"
                    )

                    # Track best match
                    if (
                        match_percent >= match_threshold
                        and match_percent > best_match_percent
                    ):
                        best_match_path = item_path
                        best_match_percent = match_percent

            except Exception as e:
                logger.debug(f"Error checking subdirectory {item}: {e}")
                continue

    return best_match_path, best_match_percent


def _find_matching_file_in_directory(
    directory: str,
    target_title: str,
    match_threshold: float,
    fuzzlogger: logging.Logger,
) -> "tuple[str, float]":
    """
    Search a directory for a file that matches the target title.

    Args:
        directory: Directory to search
        target_title: Normalized title to match against
        match_threshold: Minimum match percentage to consider valid
        fuzzlogger: Logger for fuzzy matching debug messages

    Returns:
        Tuple of (matched_file_path, match_percent)
        Returns ("", 0) if no match found
    """
    for _f in listdir(directory):
        f = enforce_str(_f)  # Ensure string for path operations
        if _is_valid_media_file(f, book_type="ebook", include_archives=True):
            filename_stem, _ = _tokenize_file(f)
            normalized_filename = _normalize_title(filename_stem)
            match_percent = _calculate_fuzzy_match(target_title, normalized_filename, fuzzlogger)
            is_match = match_percent >= match_threshold

            fuzzlogger.debug(
                f"{round(match_percent, 2)}% match {target_title} : {normalized_filename}"
            )

            if is_match:
                return os.path.join(directory, str(f)), match_percent

    return "", 0


def _create_and_cache_cover(
    dest_file: str, media_type: BookType, pagenum=1
) -> str | None:
    """
    Create and cache a cover image for comics/magazines.

    Args:
        dest_file: Path to the media file
        media_type: "comic" or "magazine"
        pagenum: Page number to use for cover (default: 1)

    Returns:
        Cached cover path (e.g., "cache/comic/abc123.jpg") or None
    """
    coverfile = create_mag_cover(dest_file, pagenum=pagenum, refresh=True)

    if not coverfile:
        return None

    # need cache folder as "magazine" not "BookType.MAGAZINE"
    sub_cache = media_type.value
    myhash = uuid.uuid4().hex
    hashname = os.path.join(DIRS.CACHEDIR, sub_cache, f"{myhash}.jpg")
    shutil.copyfile(coverfile, hashname)
    setperm(hashname)
    createthumbs(hashname)

    return f"cache/{sub_cache}/{myhash}.jpg"


def _update_issue_database(
    db,
    media_type: BookType,
    book_id: str,
    issue_id: str,
    dest_file: str,
    coverfile: str,
    older: int,
    aux_info="",
) -> None:
    """
    Update database for comic/magazine issues.

    Args:
        db: Database connection
        media_type: "comic" or "magazine"
        book_id: Comic/Magazine ID
        issue_id: Issue identifier
        dest_file: Path to processed file
        coverfile: Path to cached cover
        older: Whether this is an older issue than current
        aux_info: Additional info (used for magazines)
    """
    if media_type == BookType.COMIC:
        # Update comics table
        control_value_dict = {"ComicID": book_id}
        if older:
            new_value_dict = {
                "LastAcquired": today(),
                "IssueStatus": CONFIG["FOUND_STATUS"],
            }
        else:
            new_value_dict = {
                "LatestIssue": issue_id,
                "LastAcquired": today(),
                "LatestCover": coverfile,
                "IssueStatus": CONFIG["FOUND_STATUS"],
            }
        db.upsert("comics", new_value_dict, control_value_dict)

        # Update comicissues table
        control_value_dict = {"ComicID": book_id, "IssueID": issue_id}
        new_value_dict = {
            "IssueAcquired": today(),
            "IssueFile": dest_file,
            "Cover": coverfile,
        }
        db.upsert("comicissues", new_value_dict, control_value_dict)

    elif media_type == BookType.MAGAZINE:
        # Create issue ID
        issueid = create_id(f"{book_id} {aux_info}")

        # Update issues table
        control_value_dict = {"Title": book_id, "IssueDate": aux_info}
        new_value_dict = {
            "IssueAcquired": today(),
            "IssueFile": dest_file,
            "IssueID": issueid,
            "Cover": coverfile,
        }
        db.upsert("issues", new_value_dict, control_value_dict)

        # Update magazines table
        control_value_dict = {"Title": book_id}
        if older:
            new_value_dict = {
                "LastAcquired": today(),
                "IssueStatus": CONFIG["FOUND_STATUS"],
            }
        else:
            new_value_dict = {
                "LastAcquired": today(),
                "IssueStatus": CONFIG["FOUND_STATUS"],
                "IssueDate": aux_info,
                "LatestCover": coverfile,
            }
        db.upsert("magazines", new_value_dict, control_value_dict)


def _should_delete_processed_files(book_path, download_dir) -> "tuple[bool, str]":
    """
    Determine if processed files should be deleted based on configuration.

    Args:
        book_path: Path to the processed files
        download_dir: Root download directory

    Returns:
        Tuple of (should_delete, deletion_path)
        - should_delete: Whether files should be deleted
        - deletion_path: The path that should be deleted (may differ from book_path)
    """
    # Always delete unpacked files
    if ".unpack" in book_path:
        book_path = f"{book_path.split('.unpack')[0]}.unpack"
        return True, book_path

    # Don't delete if DESTINATION_COPY is enabled (keep source files)
    if CONFIG.get_bool("DESTINATION_COPY"):
        return False, book_path

    # Don't delete if path is the download root directory
    if book_path == download_dir.rstrip(os.sep):
        return False, book_path

    # Walk up subdirectories to find the top-level folder to delete
    deletion_path = book_path
    if deletion_path.startswith(download_dir) and ".unpack" not in deletion_path:
        while os.path.dirname(deletion_path) != download_dir.rstrip(os.sep):
            deletion_path = os.path.dirname(deletion_path)

    return True, deletion_path


def _cleanup_successful_download(book_path, download_dir, book_state, logger) -> None:
    """
    Clean up files after successful processing.

    Args:
        book_path: Path to processed files
        download_dir: Root download directory
        book_state: BookState instance with download info
        logger: Logger instance
    """
    should_delete, deletion_path = _should_delete_processed_files(
        book_path, download_dir
    )

    logger.debug(f"To Delete: {deletion_path} {should_delete}")

    if should_delete:
        try:
            shutil.rmtree(deletion_path, ignore_errors=True)
            logger.debug(
                f"Deleted {deletion_path} for {book_state.download_title}, "
                f"{book_state.mode_type} from {book_state.source}"
            )
        except Exception as why:
            logger.warning(
                f"Unable to remove {deletion_path}, {type(why).__name__} {why!s}"
            )
    elif CONFIG.get_bool("DESTINATION_COPY"):
        logger.debug(f"Not removing {deletion_path} as Keep Files is set")
    else:
        logger.debug(f"Not removing {deletion_path} as in download root")


def _send_download_notifications(
    book_state: BookState, book_type: str, global_name: str, notification_id: str
) -> None:
    """
    Send all notifications for a successful download.

    Args:
        book_state: BookState instance with download info
        book_type: Type of media
        global_name: Formatted name for the downloaded item
        notification_id: Book/Issue ID for notifications
    """
    dispname = book_state.get_display_name(CONFIG)

    custom_notify_download(f"{book_state.book_id} {book_type}")
    notify_download(
        f"{book_type} {global_name} from {dispname} at {now()}",
        notification_id,
    )
    mailing_list(book_type, global_name, notification_id)
    _update_downloads_provider_count(book_state.download_provider)


def _handle_failed_processing(
    book_state: BookState,
    book_path: str,
    metadata: BookMetadata,
    dest_file: str,
    db,
    logger: logging.Logger,
) -> None:
    """
    Handle cleanup and notifications for failed processing.

    Args:
        book_state: BookState instance with download info
        book_path: Path to processed files
        metadata: BookMetadata object with book information
        dest_file: Error message or destination file path
        db: Database connection
        logger: Logger instance
    """
    global_name = metadata.global_name
    book_type_enum = metadata.book_type_enum
    book_type_str = metadata.book_type
    logger.error(
        f"Postprocessing for {global_name!r} has failed: {dest_file!r}"
    )

    # Mark failure in BookState
    book_state.mark_failed("destination", dest_file)

    # Send failure notifications
    dispname = book_state.get_display_name(CONFIG)
    custom_notify_snatch(f"{book_state.book_id} {book_type_str}", fail=True)
    notify_snatch(
        f"{book_type_str} {global_name} from {dispname} at {now()}",
        fail=True,
    )

    # Update database status to Failed
    control_value_dict = {
        "NZBurl": book_state.download_url,
        "Status": "Snatched",
    }
    new_value_dict = {
        "Status": "Failed",
        "DLResult": enforce_str(make_unicode(dest_file)),
        "NZBDate": now(),
    }
    db.upsert("wanted", new_value_dict, control_value_dict)

    # Reset book status to Wanted so we can try a different version
    if book_type_enum == BookType.EBOOK:
        db.action(
            "UPDATE books SET status='Wanted' WHERE BookID=?",
            (book_state.book_id,),
        )
    elif book_type_enum == BookType.AUDIOBOOK:
        db.action(
            "UPDATE books SET audiostatus='Wanted' WHERE BookID=?",
            (book_state.book_id,),
        )

    # Handle failed download cleanup
    _cleanup_failed_download(book_path, logger)


def _cleanup_failed_download(book_path, logger) -> None:
    """
    Clean up files from a failed download.

    Either deletes the files or moves them to a .fail directory based on config.

    Args:
        book_path: Path to the failed download files
        logger: Logger instance
    """
    if CONFIG.get_bool("DEL_DOWNLOADFAILED"):
        logger.debug(f"Deleting {book_path}")
        shutil.rmtree(book_path, ignore_errors=True)
    else:
        # Move to .fail directory for manual inspection
        fail_path = f"{book_path}.fail"
        logger.debug(f"Making sure {fail_path} is clean")
        shutil.rmtree(fail_path, ignore_errors=True)

        try:
            _ = safe_move(book_path, fail_path)
            logger.warning(f"Residual files remain in {fail_path}")
        except Exception as why:
            logger.error(
                f"Unable to rename {book_path!r}, {type(why).__name__} {why!s}"
            )

            # Diagnose permission issues
            if not os.access(syspath(book_path), os.R_OK):
                logger.error(f"{book_path!r} is not readable")
            if not os.access(syspath(book_path), os.W_OK):
                logger.error(f"{book_path!r} is not writeable")
            if not os.access(syspath(book_path), os.X_OK):
                logger.error(f"{book_path!r} is not executable")

            # Test parent directory writability
            parent = os.path.dirname(book_path)
            try:
                test_file = os.path.join(parent, "ll_temp")
                with open(syspath(test_file), "w", encoding="utf-8") as f:
                    f.write("test")
                remove_file(test_file)
            except Exception as why:
                logger.error(f"Parent Directory {parent} is not writeable: {why}")

            logger.warning(f"Residual files remain in {book_path}")


def _try_match_candidate_file(
    candidate_file,
    book_state: BookState,
    download_dir: str,
    match_threshold: float,
    logger: logging.Logger,
    fuzzlogger: logging.Logger,
) -> "tuple[bool, float]":
    """
    Try to match a candidate file/folder against the target book.

    Performs fuzzy matching on filename, and if no match but it's a directory,
    drills down to search for matches:
    1. First tries matching subdirectories (for series/collection folders)
    2. Then tries matching files at root level (for flat collections)

    This supports:
    - Audiobook series with each book in subdirectory
    - Ebook series with each book in subdirectory
    - Flat ebook/audiobook collections (files at root)

    Args:
        candidate_file: Filename in download directory
        book_state: BookState being matched
        download_dir: Download directory path
        match_threshold: Minimum match percentage
        logger: Logger for general logging
        fuzzlogger: Logger for fuzzy matching

    Returns:
        Tuple of (is_match, match_percent)
    """
    book_state.update_candidate(os.path.join(download_dir, candidate_file))

    fuzzlogger.debug(f"Checking candidate {candidate_file}")
    filename_stem, extn = _tokenize_file(candidate_file)

    skipped_extensions = get_list(CONFIG["SKIPPED_EXT"])
    if extn in skipped_extensions:
        logger.debug(f"Skipping {candidate_file}, extension not considered")
        return False, 0

    # Fuzzy match the candidate filename
    normalized_candidate = _normalize_title(filename_stem)
    match_percent = _calculate_fuzzy_match(
        book_state.download_title, normalized_candidate, fuzzlogger
    )
    is_match = match_percent >= match_threshold

    fuzzlogger.debug(
        f"{round(match_percent, 2)}% match {book_state.download_title} : {normalized_candidate}"
    )

    # If no match and it's a directory, drill down to find the right book
    if not is_match and path_isdir(book_state.candidate_ptr or ""):
        logger.debug(f"{candidate_file} is a directory, checking contents")

        book_type_str = book_state.get_book_type_str()
        if not book_type_str:
            return False, 0

        # Use actual book title for drill-down if available, otherwise use download title
        # This is critical for collections where download name != individual book name
        search_title = (
            book_state.book_title
            if book_state.book_title
            else book_state.download_title
        )

        # _normalize_title now handles stripping known extensions intelligently
        search_title = _normalize_title(search_title)

        # Try 1: Match subdirectories (for collections organized in folders)
        # This is common for audiobook series and some ebook collections
        matched_subdir, subdir_match_percent = _find_matching_subdir(
            book_state.candidate_ptr or "",
            search_title,
            match_threshold,
            book_type_str,
            fuzzlogger,
        )

        if matched_subdir:
            logger.debug(
                f"Found matching subdirectory: {os.path.basename(matched_subdir)}"
            )
            book_state.update_candidate(matched_subdir)
            is_match = True
            match_percent = subdir_match_percent
        else:
            # Try 2: Match files at root level (for collections with files in one directory)
            matched_file, file_match_percent = _find_matching_file_in_directory(
                book_state.candidate_ptr or "",
                search_title,
                match_threshold,
                fuzzlogger,
            )

            if matched_file:
                logger.debug(f"Found matching file: {os.path.basename(matched_file)}")
                book_state.update_candidate(matched_file)
                is_match = True
                match_percent = file_match_percent

    return is_match, match_percent


def _process_matched_directory(
    book_state: BookState,
    download_dir: str,
    match_percent: float,
    logger: logging.Logger,
    fuzzlogger: logging.Logger,
) -> "tuple[bool, str]":
    """
    Process a matched file or directory to extract and validate the media file.

    Handles:
    - Single files in download root (isolates to .unpack subdirectory)
    - Finding valid files in directories
    - Extracting archives if no valid files found
    - Handling multi-book collections
    - Final validation

    For single files in download root, selectively transfers ONLY files matching
    the book's filename to an isolated .unpack subdirectory to protect other files.

    Args:
        book_state: BookState with candidate_ptr pointing to matched file/directory
        download_dir: Download directory path
        match_percent: Initial match percentage
        logger: Logger instance
        fuzzlogger: Fuzzy match logger

    Returns:
        Tuple of (is_valid, skip_reason)
    """
    candidate_ptr = book_state.candidate_ptr or ""
    # Direct downloads place files in download root
    # if candidate_ptr is download_dir, handle as single file in download root
    logger.debug(f"candidate_ptr: {candidate_ptr}")
    logger.debug(f"download_dir: {download_dir}")
    logger.debug(f"download_title: {book_state.download_title}")

    if candidate_ptr and candidate_ptr.rstrip(os.sep) == get_directory('Download').rstrip(os.sep):
        download_dir = candidate_ptr
        candidate_ptr = os.path.join(candidate_ptr, book_state.download_title)
        book_state.update_candidate(candidate_ptr)

    if path_isfile(candidate_ptr):
        # It's a single file - check if it's in download root
        file_dir = os.path.dirname(candidate_ptr)

        if file_dir == download_dir.rstrip(os.sep):
            # Single file in download root - need to isolate it to protect other files
            logger.debug(f"Single file in download root: {candidate_ptr}")

            fname = os.path.basename(candidate_ptr)
            fname_prefix = os.path.splitext(fname)[0]

            # Remove trailing noise characters
            while fname_prefix and fname_prefix[-1] in "_.  ":
                fname_prefix = fname_prefix[:-1]

            # Determine if we should copy or move or link
            if CONFIG.get_bool("DESTINATION_COPY") or (
                book_state.is_torrent() and CONFIG.get_bool("KEEP_SEEDING")
            ):
                copy_files = True
            else:
                copy_files = False

            # Create isolated .unpack directory
            targetdir = os.path.join(download_dir, f"{md5_utf8(fname_prefix)[-8:]}.unpack")
            try:
                os.makedirs(targetdir, exist_ok=True)
            except Exception as e:
                return False, f"Failed to create isolation directory {targetdir}: {e}"

            # Selectively transfer ONLY files matching this book's name
            cnt = _transfer_matching_files(
                file_dir, targetdir, fname_prefix, copy=copy_files
            )

            if cnt:
                # Successfully isolated - update candidate to the folder
                book_state.update_candidate(targetdir)
                logger.debug(f"Isolated {cnt} file(s) to {targetdir}")
                return True, ""  # Success - file isolated to .unpack folder
            # No files transferred - cleanup empty directory
            try:
                logger.debug(f"No files moved to {targetdir}, removing it")
                os.rmdir(targetdir)
            except OSError:
                contextlib.suppress(OSError)
            return False, "Failed to isolate file to subdirectory"

        # File not in root - update candidate_ptr to parent directory
        # process_destination expects a directory, not a file
        parent_dir = os.path.dirname(book_state.candidate_ptr or "")
        book_state.update_candidate(parent_dir)
        logger.debug(f"Updated candidate from file to parent directory: {parent_dir}")
        return True, ""

    if not path_isdir(book_state.candidate_ptr):
        return False, f"Folder {book_state.candidate_ptr} is not found"

    logger.debug(
        f"Found folder ({round(match_percent, 2)}%) [{book_state.candidate_ptr}] "
        f"for {book_state.get_book_type_str()} {book_state.download_title}"
    )

    # First pass: Look for valid files
    valid_file_path = _find_valid_file_in_directory(
        book_state.candidate_ptr, book_type=book_state.get_book_type_str()
    )

    if valid_file_path:
        book_state.update_candidate(os.path.dirname(valid_file_path))
    else:
        # No valid file, try extracting archives
        new_candidate, archives_extracted = _extract_archives_in_directory(
            book_state.candidate_ptr, download_dir, book_state.download_title
        )
        book_state.update_candidate(new_candidate)

        # If we extracted archives, search again
        if archives_extracted:
            valid_file_path = _find_valid_file_in_directory(
                book_state.candidate_ptr,
                book_type=book_state.get_book_type_str(),
                recurse=True,
            )

            if not valid_file_path:
                logger.debug("No valid file after extraction")
                return False, "No valid file found after extraction"
        else:
            return False, "No valid file or archives found"

    # Handle multi-book collections for eBooks
    # If folder contains multiple books, extract ONLY the best matching one
    book_type_enum = book_state.get_book_type_enum()
    if book_type_enum == BookType.EBOOK:
        mult = multibook(book_state.candidate_ptr, recurse=True)
        if mult:
            # Use actual book title for better matching in collections
            search_title = (
                book_state.book_title
                if book_state.book_title
                else book_state.download_title
            )

            # Found collection - extract best match to isolated directory
            extracted_path, skipped, skip_reason = _extract_best_match_from_collection(
                book_state.candidate_ptr,
                search_title,
                download_dir,
                logger,
                fuzzlogger,
            )
            if skipped:
                return False, skip_reason
            book_state.update_candidate(extracted_path)
    else:
        # For non ebook types, just find the file
        book_type_str = book_state.get_book_type_str()
        if book_type_str:
            result = book_file(
                book_state.candidate_ptr or "",
                book_type_str,
                recurse=True,
                config=CONFIG,
            )
            if result:
                book_state.update_candidate(os.path.dirname(result))
            else:
                return False, f"No {book_type_enum.value} found"

    # Final validation
    is_valid, skip_reason = _validate_candidate_directory(
        book_state.candidate_ptr, logger
    )
    return is_valid, skip_reason


def _find_best_match_in_downloads(
    book_state,
    all_downloads,
    match_threshold,
    logger,
    fuzzlogger,
) -> "tuple[float, str]":
    """
    Search through all downloads to find the best match for a book.

    This is the core matching loop (Matching Stage|Second Pass i.e., the Fallback Search) that:
    1. Iterates through all candidates from all directories
    2. Fuzzy matches each candidate
    3. Searches inside directories if needed
    4. Processes matched directories (extract archives, handle collections)
    5. Tracks all matches and returns the best one

    Args:
        book_state: BookState to find matches for
        all_downloads: List of (parent_dir, filename) tuples from all directories
        match_threshold: Minimum match percentage
        logger: Logger instance
        fuzzlogger: Fuzzy match logger

    Returns:
        Tuple of (best_match_percent, skip_reason)
        Updates book_state.candidate_ptr to best match location
    """
    matches = []
    if not all_downloads:
        return 0, "No downloads found"

    book_type = book_state.get_book_type_str()
    logger.debug(f"Fuzzy searching for {book_type} across all downloads")

    for parent_dir, candidate_file in all_downloads:
        # Try to match this candidate
        is_match, match_percent = _try_match_candidate_file(
            candidate_file,
            book_state,
            parent_dir,
            match_threshold,
            logger,
            fuzzlogger,
        )

        if is_match:
            # Process matched directory (extract archives, handle collections, validate)
            is_valid, skip_reason = _process_matched_directory(
                book_state,
                parent_dir,
                match_percent,
                logger,
                fuzzlogger,
            )

            if is_valid:
                matches.append([match_percent, book_state.candidate_ptr])
                if match_percent == 100:
                    # Perfect match, no need to keep searching
                    break
            else:
                book_state.mark_skipped(skip_reason)
        # Even non-matches get tracked to report closest match
        elif match_percent > 0:
            matches.append([match_percent, book_state.candidate_ptr])

    # Find the best match
    if not matches:
        return 0, "No matches found"

    highest = max(matches, key=lambda x: x[0])
    best_match_percent = highest[0]
    best_candidate_ptr = highest[1]

    book_state.update_candidate(best_candidate_ptr)

    if best_match_percent >= match_threshold:
        logger.debug(
            f"Found match ({round(best_match_percent, 2)}%): {best_candidate_ptr} "
            f"for {book_state} {book_state.download_title}"
        )
        return best_match_percent, ""
    logger.debug(
        f"Closest match ({round(best_match_percent, 2)}%): {best_candidate_ptr}"
    )
    for match in matches:
        fuzzlogger.debug(f"Match: {round(match[0], 2)}%  {match[1]}")
    return best_match_percent, "No match above threshold"


def _process_book_post(
    metadata: EbookMetadata,
    dest_file: str,
    book_id: str,
) -> str:
    """
    Handle post-processing for ebooks and audiobooks.

    Args:
        metadata: EbookMetadata object
        dest_file: Destination file path
        book_id: Book ID

    Returns:
        book_name
    """
    if metadata.book_name and dest_file:
        _process_extras(dest_file, metadata.global_name, book_id, metadata.book_type)

    return metadata.book_name


def _process_comic_post(
    metadata: ComicMetadata, dest_file: str, mostrecentissue: str, db
) -> "tuple[str, str]":
    """
    Handle post-processing for comics.

    Args:
        metadata: ComicMetadata object
        dest_file: Destination file path
        mostrecentissue: Most recent issue date for comparison
        db: Database connection

    Returns:
        Tuple of (bookname, issueid) for notification purposes
    """
    comicid = metadata.comic_id
    issueid = metadata.issue_id

    if comicid:
        # Determine if this is an older issue
        older = int(mostrecentissue) > int(issueid) if mostrecentissue else False

        # Create and cache cover
        coverfile = _create_and_cache_cover(dest_file, BookType.COMIC, pagenum=1) or ""

        # Update database
        _update_issue_database(
            db, BookType.COMIC, comicid, issueid, dest_file, coverfile, older
        )

    return "", issueid


def _process_magazine_post(
    metadata: MagazineMetadata, dest_file: str, mostrecentissue: str, book_state, db
) -> "tuple[str, str]":
    """
    Handle post-processing for magazines.

    Args:
        metadata: MagazineMetadata object
        dest_file: Destination file path
        mostrecentissue: Most recent issue date for comparison
        book_state: BookState for aux_info access
        db: Database connection

    Returns:
        Tuple of (bookname, issueid) for notification purposes
    """
    issueid = metadata.issue_id

    if mostrecentissue:
        if mostrecentissue.isdigit() and str(book_state.aux_info).isdigit():
            older = int(mostrecentissue) > int(book_state.aux_info)
        else:
            older = mostrecentissue > book_state.aux_info
    else:
        older = False

    # Get cover page from metadata
    if CONFIG.get_bool("SWAP_COVERPAGE"):
        coverpage = 1
    else:
        coverpage = metadata.cover_page if metadata.cover_page else 1

    coverfile = (
        _create_and_cache_cover(dest_file, BookType.MAGAZINE, pagenum=coverpage) or ""
    )

    # Update database
    _update_issue_database(
        db,
        BookType.MAGAZINE,
        book_state.book_id,
        book_state.aux_info,
        dest_file,
        coverfile,
        older,
        book_state.aux_info,
    )

    # Auto-add if enabled
    if CONFIG["IMP_AUTOADDMAG"]:
        dest_path = os.path.dirname(dest_file)
        _process_auto_add(dest_path, book_type_enum=BookType.MAGAZINE)

    return "", issueid


def _process_successful_download(
    book_state: BookState,
    metadata: BookMetadata,
    dest_file,
    book_path,
    download_dir,
    mostrecentissue,
    ignoreclient: bool,
    db,
    logger: logging.Logger,
) -> int:
    """
    Handle all post-processing for a successful download.

    This includes:
    - Processing extras (for books)
    - Creating covers (for comics/magazines)
    - Updating database
    - Deleting from download client
    - Cleaning up files
    - Sending notifications

    Args:
        book_state: BookState instance
        dest_file: Destination file path
        book_path: Processing path
        download_dir: Download directory
        metadata: BookMetadata object with book information
        mostrecentissue: Most recent issue (for comics/magazines)
        ignoreclient: Whether to skip download client interaction
        db: Database connection
        logger: Logger instance

    Returns:
        1 if successfully processed, 0 otherwise
    """
    global_name = metadata.global_name
    book_type = metadata.book_type
    logger.debug(
        f"Processed {book_state.mode_type} ({book_path}): {global_name}, {book_state.download_url}"
    )
    dest_file = enforce_str(make_unicode(dest_file))

    # Update wanted table status to Processed
    control_value_dict = {
        "NZBurl": book_state.download_url,
        "Status": "Snatched",
    }
    new_value_dict = {
        "Status": "Processed",
        "NZBDate": now(),
        "DLResult": dest_file,
    }
    db.upsert("wanted", new_value_dict, control_value_dict)

    # Type-specific post-processing
    if isinstance(metadata, EbookMetadata):
        bookname = _process_book_post(metadata, dest_file, book_state.book_id)
        issueid = 0
    elif isinstance(metadata, ComicMetadata):
        bookname, issueid = _process_comic_post(
            metadata, dest_file, mostrecentissue, db
        )
    elif isinstance(metadata, MagazineMetadata):
        bookname, issueid = _process_magazine_post(
            metadata, dest_file, mostrecentissue, book_state, db
        )
    else:
        # Unknown metadata type (should never happen)
        bookname = ""
        issueid = 0

    # Delete from download client if appropriate
    to_delete = True
    if ignoreclient is False and to_delete:
        if book_state.can_delete_from_client():
            book_state.progress, book_state.finished = get_download_progress(
                book_state.source, book_state.download_id
            )
            logger.debug(
                f"Progress for {book_state.download_title} {book_state.progress}/{book_state.finished}"
            )

            if isinstance(book_state.progress, int) and book_state.progress == 100:
                _update_download_status(book_state, db, logger)
            elif isinstance(book_state.progress, int) and book_state.progress < 0:
                logger.debug(
                    f"{book_state.download_title} not found at {book_state.source}"
                )
        elif not book_state.source:
            logger.warning(f"Unable to remove {book_state.download_title}, no source")
        elif book_state.source != 'DIRECT':  # direct sources don't have anything to remove
            logger.warning(
                f"Unable to remove {book_state.download_title} from {book_state.source}, no DownloadID"
            )

    # Clean up source files
    _cleanup_successful_download(book_path, download_dir, book_state, logger)

    logger.info(f"Successfully processed:{global_name}")

    # Send notifications
    notification_id = book_state.book_id if bookname else str(issueid)
    _send_download_notifications(book_state, book_type, global_name, notification_id)

    # Mark as successfully processed
    book_state.mark_processed()

    return 1


def _process_book_after_matching(
    book_state: BookState,
    parent_dir: str,
    ignoreclient: bool,
    db,
    logger: logging.Logger,
):
    """
    Process a book after candidate location has been found and validated.

    This is the common path used after both targeted and fallback search.
    Handles metadata retrieval, destination processing, and post-processing.

    Args:
        book_state: BookState with candidate_ptr set to valid location
        parent_dir: Parent directory for processing context
        ignoreclient: Whether to skip download client interaction
        db: Database connection
        logger: Logger instance

    Returns:
        Number of books processed (1 if successful, 0 if failed)
    """
    # Retrieve metadata and prepare destination paths
    if book_state.is_book():  # eBook or Audiobook
        metadata = prepare_book_metadata(
            book_state.book_id, book_state.get_book_type_str(), db
        )
        if not metadata:
            logger.warning(f"Unable to retrieve metadata for {book_state.book_id}")
            book_state.mark_failed(
                "metadata", f"Book {book_state.book_id} not found in database"
            )
            return 0
        mostrecentissue = ""

    elif book_state.is_magazine():  # Magazine
        metadata = prepare_magazine_metadata(
            book_state.book_id, book_state.aux_info, db
        )
        if not metadata or not metadata.dest_path:
            logger.warning(
                f"Unable to retrieve magazine metadata for {book_state.book_id}"
            )
            book_state.mark_failed(
                "metadata", f"Magazine {book_state.book_id} not found in database"
            )
            return 0
        if not make_dirs(metadata.dest_path):
            logger.warning(f"Unable to create directory {metadata.dest_path}")
            book_state.mark_failed(
                "metadata", f"Cannot create directory {metadata.dest_path}"
            )
            return 0
        mostrecentissue = metadata.most_recent_issue

    else:  # Comic
        metadata = prepare_comic_metadata(book_state.book_id, db)
        if not metadata:
            emsg = f'Nothing in database matching "{book_state.book_id}"'
            logger.debug(emsg)
            book_state.mark_failed("metadata", emsg)
            control_value_dict = {"BookID": book_state.book_id, "Status": "Snatched"}
            new_value_dict = {"Status": "Failed", "NZBDate": now(), "DLResult": emsg}
            db.upsert("wanted", new_value_dict, control_value_dict)
            return 0
        if not make_dirs(metadata.dest_path):
            logger.warning(f"Unable to create directory {metadata.dest_path}")
            book_state.mark_failed(
                "metadata", f"Cannot create directory {metadata.dest_path}"
            )
            return 0
        mostrecentissue = metadata.most_recent_issue
        logger.debug(f"Processing {metadata.title} issue {metadata.issue_date}")

    # Process the downloaded files and move them to the target destination
    success, dest_file, book_path = _process_destination(
        book_metadata=metadata,
        book_path=book_state.candidate_ptr or "",
        logger=logger,
        mode=book_state.mode_type,
    )

    if success:
        # Handle successful processing
        return _process_successful_download(
            book_state,
            metadata,
            dest_file,
            book_path,
            parent_dir,
            mostrecentissue,
            ignoreclient,
            db,
            logger,
        )
    # Handle failed processing
    _handle_failed_processing(
        book_state, book_path, metadata, dest_file, db, logger
    )
    return 0


def _process_snatched_book(
    book_state,
    all_downloads,
    ignoreclient: bool,
    db,
    logger,
    fuzzlogger,
) -> int:
    """
    Process a single snatched book using fallback fuzzy search (Matching Stage|Second pass).

    This is called when targeted search fails or isn't available.
    Fuzzy matches against all compiled downloads from all directories.

    Args:
        book_state: BookState instance for tracking the item being processed
        all_downloads: List of (parent_dir, filename) tuples from all directories
        ignoreclient: Whether to skip download client interaction
        db: Database connection
        logger: Logger instance
        fuzzlogger: Fuzzy match logger

    Returns:
        Number of items successfully processed (0 or 1)
    """
    match_threshold = CONFIG.get_int("DLOAD_RATIO")

    # Find best matching candidate across all downloads
    match_percent, skip_reason = _find_best_match_in_downloads(
        book_state,
        all_downloads,
        match_threshold,
        logger,
        fuzzlogger,
    )

    if match_percent < match_threshold:
        logger.debug(f"No match found for {book_state.download_title}: {skip_reason}")
        book_state.mark_failed(
            "matching",
            f"No match above {match_threshold}% threshold (best: {match_percent}%)",
        )
        return 0

    # Match found - derive parent_dir from matched candidate_ptr
    parent_dir = os.path.dirname(book_state.candidate_ptr.rstrip(os.sep))
    if not path_isdir(book_state.candidate_ptr):
        # It's a file, get its parent directory
        parent_dir = os.path.dirname(book_state.candidate_ptr)

    # Continue with common post-matching processing
    return _process_book_after_matching(
        book_state, parent_dir, ignoreclient, db, logger
    )


def _calculate_download_age(snatched_date: str) -> tuple:
    """
    Calculate time elapsed since download was snatched.

    Args:
        snatched_date: NZBdate string in format 'YYYY-MM-DD HH:MM:SS'

    Returns:
        Tuple of (hours, minutes, total_seconds)
        Returns (0, 0, 0) if date parsing fails
    """
    try:
        when_snatched = datetime.datetime.strptime(snatched_date, "%Y-%m-%d %H:%M:%S")
        timenow = datetime.datetime.now()
        td = timenow - when_snatched
        diff = td.total_seconds()  # time difference in seconds
    except ValueError:
        diff = 0

    hours = int(diff / 3600)
    mins = int(diff / 60)

    return hours, mins, diff


def _handle_seeding_status(
    book_state: BookState,
    keep_seeding: bool,
    wait_for_seeding: bool,
    db,
    logger: logging.Logger,
) -> bool:
    """
    Handle downloads in 'Seeding' status.

    Checks if seeding is complete and handles:
    - Torrents removed from client (progress < 0)
    - Seeding completion based on config
    - File cleanup after seeding
    - Database status updates

    Args:
        book_state: SimpleNamespace with download state
        keep_seeding: CONFIG['KEEP_SEEDING'] value
        wait_for_seeding: CONFIG['SEED_WAIT'] value
        db: Database connection
        logger: Logger instance

    Returns:
        True if item should be skipped (still seeding), False otherwise
    """
    logger.debug(
        f"Progress:{book_state.progress} Finished:{book_state.finished} "
        f"Waiting:{wait_for_seeding} Keep Seeding: {keep_seeding}"
    )

    """
        Is changing to Snatched really necessary? If the download has already been processed
        while still seeding, and the files are not moved, we will end up processing it twice
    """
    # Handle case where torrent not found in client (was removed after seeding or communication issue)
    if isinstance(book_state.progress, int) and book_state.progress < 0:
        # -1 signals not found, -2 communication issue, can be retried
        # Files should still be on disk, but file processing loop has already run
        # Change status to Snatched if -1, so file matching logic will run next cycle to find and process files
        msg = f"{book_state.download_title} not found at {book_state.source}, "
        if book_state.progress == -1:
            msg += "torrent was removed, changing status to Snatched to process files from download directory"
            if book_state.book_id != "unknown":
                cmd = "UPDATE wanted SET status='Snatched' WHERE status='Seeding' and DownloadID=?"
                db.action(cmd, (book_state.download_id,))
        else:
            msg += "communication issue, will retry on next run"
        logger.info(msg)

        # File matching will process it next cycle
        return True  # Skip to next item

    # Handle normal seeding completion
    if book_state.finished or (not keep_seeding and not wait_for_seeding):
        if book_state.finished:
            logger.debug(
                f"{book_state.download_title} finished seeding at {book_state.source}"
            )
        else:
            logger.debug(
                f"{book_state.download_title} not seeding at {book_state.source}"
            )

        if CONFIG.get_bool("DEL_COMPLETED"):
            logger.debug(
                f"Removing seeding completed {book_state.download_title} from {book_state.source}"
            )

            delfiles = not CONFIG.get_bool("DESTINATION_COPY")
            delete_task(
                book_state.source,
                book_state.download_id,
                delfiles,
            )

        if book_state.book_id != "unknown":
            cmd = "UPDATE wanted SET status='Processed',NZBDate=? WHERE status='Seeding' and DownloadID=?"
            db.action(cmd, (now(), book_state.download_id))
            logger.info(
                f"STATUS: {book_state.download_title} [Seeding -> Processed] Seeding complete"
            )

        # only delete the files if not in download root dir and DESTINATION_COPY not set
        # This is for downloaders (rtorrent) that don't let us tell them to delete files
        # NOTE it will silently fail if the torrent client downloadfolder is not local
        # e.g. in a docker or on a remote machine
        book_path = get_download_folder(book_state.source, book_state.download_id)
        if CONFIG.get_bool("DESTINATION_COPY"):
            logger.debug("Not removing original files as Keep Files is set")
        elif book_path in get_list(CONFIG["DOWNLOAD_DIR"]):
            logger.debug("Not removing original files as in download root")
        else:
            shutil.rmtree(book_path, ignore_errors=True)
            logger.debug(
                f"Deleted {book_path} for {book_state.download_title}, {book_state.mode_type} from {book_state.source}"
            )
        return True  # Skip to next item
    logger.debug(
        f"{book_state.download_title} still seeding at {book_state.source}"
    )
    return True  # Skip to next item


def _handle_snatched_timeout(
    book_state, hours: int, mins: int, max_hours: int, logger: logging.Logger
) -> tuple:
    """
    Handle timeout logic for downloads in 'Snatched' status.

    Determines if a snatched download should be aborted based on:
    - Time since snatched
    - Download progress
    - Whether torrent exists in client

    For downloads at 100% that timed out, attempts direct processing.

    Args:
        book_state: SimpleNamespace with download state
        hours: Hours since download was snatched
        mins: Minutes since download was snatched
        max_hours: CONFIG['TASK_AGE'] maximum age before abort
        logger: Logger instance

    Returns:
        Tuple of (should_abort, should_skip_to_next)
        should_abort: True if download should be aborted
        should_skip_to_next: True if we should continue to next item (processed successfully)
    """
    should_abort = False
    should_skip = False
    short_wait = 5
    longer_wait = 30

    # has it been aborted (wait a short while before checking)
    if mins > short_wait and isinstance(book_state.progress, int) and book_state.progress < 0:
        # Torrent/download not found in client
        # Give slow magnets and client issues more time before aborting
        if mins < longer_wait:
            # Less than 30 minutes - could be slow magnet link or temporary client issue
            logger.debug(
                f"{book_state.download_title} not found at {book_state.source} but only "
                f"{mins} {plural(mins, 'minute')} old, waiting for torrent to appear"
            )
            should_abort = False
        else:
            # Over 30 minutes and never appeared - probably failed to add or was rejected
            logger.warning(
                f"{book_state.download_title} not found at {book_state.source} after "
                f"{mins} {plural(mins, 'minute')}, aborting"
            )
            should_abort = True

    if max_hours and hours >= max_hours:
        # SAB can report 100% (or more) and not finished if missing blocks and needs repair
        # For torrents, check if download is complete before timing out
        # This handles edge cases where:
        # - Torrent reached 100% but files aren't accessible yet (client still moving/verifying)
        # - Race condition where torrent completes just as timeout expires
        # - Large torrents that take time to post-process and hit timeout during processing
        # - Any case where a complete torrent somehow wasn't processed in the normal flow
        if check_int(book_state.progress, 0) >= 100:
            # Download is complete - attempt direct processing if it's a book/audiobook
            logger.info(
                f"{book_state.download_title} reached timeout but is 100% complete - attempting direct processing"
            )
            should_abort = False

            # For downloads at 100%, don't abort - let normal processing retry next cycle
            logger.debug(
                f"{book_state.download_title} at 100% will retry on next postprocessor run"
            )

        elif check_int(book_state.progress, 0) < 95:
            # Less than 95% after timeout - likely stuck
            should_abort = True

        elif (
            hours >= max_hours + 1
        ):  # Progress is 95-99% so let's give it an extra hour
            # Still not complete after extended timeout
            should_abort = True

    return should_abort, should_skip


def _handle_aborted_download(
    book_state: BookState, hours: int, db, logger: logging.Logger
) -> None:
    """
    Handle downloads marked as 'Aborted'.

    Updates database, sends notifications, and optionally deletes
    the failed download task from the download client.

    Args:
        book_state: SimpleNamespace with download state
        hours: Hours since download was snatched (for error message)
        db: Database connection
        logger: Logger instance
    """
    dlresult = ""
    if book_state.source and book_state.source != "DIRECT":
        if book_state.status == "Snatched":
            progress = f"{book_state.progress}"
            if progress.isdigit():  # could be "Unknown" or -1
                progress += "%"
            dlresult = (
                f"{book_state.download_title} was sent to {book_state.source} {hours} hours ago. "
                f"Progress: {progress}"
            )
            if check_int(book_state.progress, 0) == 100:  # Fixed typo from chech_int
                dlresult += " Please check download directory is correct"
        else:
            dlresult = f"{book_state.download_title} was aborted by {book_state.source}"

    custom_notify_snatch(f"{book_state.book_id} {book_state.source}", fail=True)
    notify_snatch(
        f"{book_state.download_title} from {book_state.source} at {now()}", fail=True
    )

    # change status to "Failed", and ask downloader to delete task and files
    # Only reset book status to wanted if still snatched in case another download task succeeded
    if book_state.book_id != "unknown":
        cmd = ""
        book_type_enum = book_state.get_book_type_enum()
        if book_type_enum == BookType.EBOOK:
            cmd = (
                "UPDATE books SET status='Wanted' WHERE status='Snatched' and BookID=?"
            )
        elif book_type_enum == BookType.AUDIOBOOK:
            cmd = "UPDATE books SET audiostatus='Wanted' WHERE audiostatus='Snatched' and BookID=?"
        if cmd:
            db.action(cmd, (book_state.book_id,))

        # use url and status for identifier because magazine id isn't unique
        if book_state.status == "Snatched":
            q = "UPDATE wanted SET Status='Failed',DLResult=? WHERE NZBurl=? and Status='Snatched'"
            db.action(q, (dlresult, book_state.download_url))
        else:  # don't overwrite dlresult reason for the abort
            q = "UPDATE wanted SET Status='Failed' WHERE NZBurl=? and Status='Aborted'"
            db.action(q, (book_state.download_url,))

        if CONFIG.get_bool("DEL_FAILED"):
            logger.warning(f"{dlresult}, deleting failed task")
            delete_task(book_state.source, book_state.download_id, True)


def _check_and_schedule_next_run(db, logger: logging.Logger, reset: bool) -> None:
    """
    Determine if postprocessor should run again.

    Checks for remaining snatched/seeding items and schedules
    the PostProcessor job accordingly (STOP, RESTART, or continue).

    Args:
        db: Database connection
        logger: Logger instance
        reset: Whether to force restart
    """

    # Check if postprocessor needs to run again
    snatched = db.select("SELECT * from wanted WHERE Status='Snatched'")
    seeding = db.select("SELECT * from wanted WHERE Status='Seeding'")

    if not len(snatched) and not len(seeding):
        logger.info("Nothing marked as snatched or seeding. Stopping postprocessor.")
        schedule_job(SchedulerCommand.STOP, target="PostProcessor")
    elif len(seeding):
        logger.info(f"Seeding {len(seeding)}")
        schedule_job(SchedulerCommand.RESTART, target="PostProcessor")
    elif reset:
        schedule_job(SchedulerCommand.RESTART, target="PostProcessor")


def _manage_download_status(db, logger: logging.Logger) -> None:
    """
    Manage download lifecycle for incomplete/failed downloads.

    Handles three status types:
    - Seeding: Check completion, handle removed torrents, update status
    - Snatched: Check timeouts, attempt direct processing if 100% complete
    - Aborted: Send failure notifications, clean up

    This runs after main processing to handle items that couldn't be processed
    or are still in progress.

    Args:
        db: Database connection
        logger: Logger instance
    """
    # Query for items needing status management
    cmd = "SELECT * from wanted WHERE Status IN ('Snatched', 'Aborted', 'Seeding')"
    incomplete = db.select(cmd)
    logger.info(f"Found {len(incomplete)} items for status management")

    # Get config values once
    keep_seeding = CONFIG.get_bool("KEEP_SEEDING")
    wait_for_seeding = CONFIG.get_bool("SEED_WAIT")
    max_hours = CONFIG.get_int("TASK_AGE")

    for book_row in incomplete:
        book_dict = dict(book_row)

        # Use BookState for consistency with main processing loop
        book_state = BookState.from_db_row(book_row, CONFIG)

        # Set runtime fields for download status tracking
        book_state.aborted = False
        book_state.finished = False
        book_state.progress = "Unknown"
        book_state.skipped_reason = book_dict.get("skipped", "")

        logger.debug(
            f"{book_state.status} {book_state.source} {book_state.download_title}"
        )

        # Get progress from download client
        if book_state.status == "Aborted":
            book_state.aborted = True
        else:
            book_state.progress, book_state.finished = get_download_progress(
                book_state.source, book_state.download_id
            )

        # Route to appropriate handler based on status
        if book_state.status == "Seeding":
            should_skip = _handle_seeding_status(
                book_state, keep_seeding, wait_for_seeding, db, logger
            )
            if should_skip:
                continue

        elif book_state.status == "Snatched":
            hours, mins, _ = _calculate_download_age(book_state.snatched_date)
            should_abort, should_skip = _handle_snatched_timeout(
                book_state, hours, mins, max_hours, logger
            )

            if should_skip:
                continue  # Successfully processed, move to next

            if should_abort:
                book_state.aborted = True

        # Handle aborted downloads
        if book_state.aborted:
            hours, mins, _ = _calculate_download_age(book_state.snatched_date)
            _handle_aborted_download(book_state, hours, db, logger)
        elif book_state.status == "Snatched":
            # Log progress for items still downloading
            hours, mins, _ = _calculate_download_age(book_state.snatched_date)
            if mins:
                provider = book_state.source
                if book_state.source == "DIRECT":
                    provider = book_state.download_provider
                logger.debug(
                    f"{book_state.download_title} was sent to {provider} {mins} {plural(mins, 'minute')} ago."
                    f" Progress {book_state.progress} {book_state.skipped_reason}"
                    f" Status {book_state.status}"
                )


def _search_in_known_location(
    book_state: BookState,
    ignoreclient: bool,
    db,
    logger: logging.Logger,
    fuzzlogger: logging.Logger,
):
    """
    Search for book in client-provided download_folder (ratching Stage:First Pass e.g., the Targeted Search).

    Trusts client location even if outside configured directories.
    Uses book_title for drill-down matching in collections.

    Args:
        book_state: BookState with download_folder populated
        ignoreclient: Whether to skip download client interaction
        db: Database connection
        logger: Logger instance
        fuzzlogger: Fuzzy logger

    Returns:
        Number of books processed (1 if successful, 0 if failed)
    """
    logger.info(f"Download folder: {book_state.download_folder}")
    logger.info(f"Book title: {book_state.book_title}")

    # Validate folder exists
    if not path_exists(book_state.download_folder):
        logger.warning(f"FAIL: Download folder not found: {book_state.download_folder}")
        return 0

    # Check if folder has content
    try:
        contents = listdir(book_state.download_folder)
        if not contents:
            logger.warning("FAIL: Download folder is empty")
            return 0
        logger.info(f"Folder has {len(contents)} items")
    except Exception as e:
        logger.error(f"ERROR: Cannot access folder: {e}")
        return 0

    # Set candidate_ptr to the known folder
    book_state.update_candidate(book_state.download_folder)
    logger.debug(f"Candidate folder set to: {book_state.candidate_ptr}")

    # Get parent directory for processing context
    parent_dir = os.path.dirname(book_state.download_folder.rstrip(os.sep))
    logger.debug(f"Parent directory: {parent_dir}")

    # Process the known folder (drill-down uses book_title, extraction, validation)
    is_valid, skip_reason = _process_matched_directory(
        book_state,
        parent_dir,
        100.0,  # 100% - we trust the client
        logger,
        fuzzlogger,
    )

    if not is_valid:
        logger.warning(f"FAIL: Validation failed: {skip_reason}")
        book_state.mark_failed("validation", skip_reason)
        return 0

    logger.debug("Validation passed, continuing to metadata and processing")

    # Continue with metadata and processing
    result = _process_book_after_matching(
        book_state, parent_dir, ignoreclient, db, logger
    )

    return result


def _compile_all_downloads(dirlist, logger):
    """
    Compile all downloads from configured directories into a single list.

    Handles OSError gracefully by skipping inaccessible directories.

    Args:
        dirlist: List of download directories to scan
        logger: Logger instance

    Returns:
        List of (parent_dir, filename) tuples
    """
    all_downloads = []

    for download_dir in dirlist:
        try:
            downloads = listdir(download_dir)
            all_downloads.extend([(download_dir, f) for f in downloads])
            logger.debug(f"Found {len(downloads)} items in {download_dir}")
        except OSError as why:  # noqa: PERF203
            logger.error(
                f"Could not access [{download_dir}]: {why.strerror} - skipping"
            )
            continue

    logger.info(
        f"Compiled {len(all_downloads)} total items from {len(dirlist)} download {plural(len(dirlist), 'directory')}"
    )
    return all_downloads


# noinspection PyBroadException
def process_dir(reset=False, startdir=None, ignoreclient=False, downloadid=None):
    """
    Main postprocessor entry point with book-centric workflow.

    Pass 1: Process completed snatched downloads
        - Handles deliberately downloaded books (from search/snatch)
        - Fuzzy matches download_title against filesystem to find location
        - Uses book_title for drill-down matching in collections
        - Handles single books AND collections (extracts best match)
        - Processes all media types: ebook, audiobook, magazine, comic
        - Extracts archives, handles multipart, searches obfuscated folders
        - Moves/copies to library, sends notifications

    Pass 2: Process unsnatched books with LL.(bookid) naming
        - Scans for folders/files with "LL.(bookid)" pattern
        - These are manually added or leftover books NOT in wanted table
        - Imports to library if not already present

    Pass 3: Handle download status management
        - Seeding: Check completion, handle removed torrents
        - Snatched: Check timeouts (100% complete downloads retry next cycle)
        - Aborted: Send notifications, clean up

    Args:
        reset: Force postprocessor to restart after completion
        startdir: Specific directory to process (overrides config)
        ignoreclient: Skip download client interaction
        downloadid: Process specific download ID only
    """
    logger = logging.getLogger(__name__)
    postprocesslogger = logging.getLogger("special.postprocess")
    fuzzlogger = logging.getLogger("special.fuzz")

    # Thread safety check - prevent concurrent execution
    count = 0
    logger.debug("Attempt to run POSTPROCESS")
    for name in [t.name for t in threading.enumerate()]:
        if name == "POSTPROCESS":
            count += 1

    incoming_threadname = thread_name()
    if incoming_threadname == "POSTPROCESS":
        count -= 1

    if count:
        logger.debug("POSTPROCESS is already running")
        return  # Exit early if already running

    logger.debug("No concurrent POSTPROCESS threads detected")

    # Set thread name for this execution
    thread_name("POSTPROCESS")

    db = database.DBConnection()
    try:
        logger.debug(f"Storing start time for {thread_name()}")
        db.upsert("jobs", {"Start": time.time()}, {"Name": thread_name()})

        # Now we will get a list of wanted books that are snatched and ready for processing
        if downloadid:
            snatched_books = db.select(
                "SELECT * from wanted WHERE DownloadID=? AND Status='Snatched'",
                (downloadid,),
            )
        else:
            snatched_books = db.select("SELECT * from wanted WHERE Status='Snatched'")

        postprocesslogger.debug(
            f'Found {len(snatched_books)} {plural(len(snatched_books), "file")} marked "Snatched"'
        )

        # ======================================================================
        #  Filtering Stage: Get snatched books that are ready for processing
        #    by removing books still downloading, rejected content, etc.
        # ======================================================================
        books_to_process = []
        if len(snatched_books):
            TELEMETRY.record_usage_data("Process/Snatched")
            books_to_process = _get_ready_from_snatched(db, snatched_books)

        postprocesslogger.info(
            f"Found {len(books_to_process)} {plural(len(books_to_process), 'book')} ready to process"
        )

        # Build the list of directories that will will use to process downloaded assets
        # At least one valid directory must be present or we will stop
        startdir_file = None
        if startdir:
            if path_isfile(startdir):
                startdir_file = startdir
                templist = [os.path.dirname(startdir)]
            else:
                templist = [startdir]
        else:
            templist = get_list(CONFIG["DOWNLOAD_DIR"], ",")
            if len(templist) and get_directory("Download") != templist[0]:
                templist.insert(0, str(get_directory("Download")))

        download_dirlist = []
        for item in templist:
            if path_isdir(item):
                download_dirlist.append(item)
            else:
                postprocesslogger.debug(f"[{item}] is not a directory")

        # Collect all entries within our download directories in a list to analyze against our
        # downloaded items. If there are no available download directories or entries in the download
        # folders, we will still process the books because there is a chance that that a book is downloading
        # to a location outside of our configured directories (e.g., the download started before a config change)
        all_downloads = None
        if download_dirlist:
            if startdir_file:
                all_downloads = [
                    (os.path.dirname(startdir_file), os.path.basename(startdir_file))
                ]
                postprocesslogger.debug(
                    f"Using single file startdir: {startdir_file}"
                )
            else:
                # Compile all downloads from all directories once
                postprocesslogger.debug(
                    f"Compiling downloads from directories: {download_dirlist}"
                )
                all_downloads = _compile_all_downloads(download_dirlist, logger)
                if not all_downloads:
                    postprocesslogger.warning(
                        "No downloads found in any configured directory"
                    )
        else:
            postprocesslogger.warning("No download directories are configured.")

        # This is where our processing of books will occur. This is a multipass process for locating
        # and performing file operations on the downloaded items

        # This will provide a little bit of padding between dl completion and processing
        processing_delay = CONFIG.get_int("PP_DELAY")

        ppcount = 0

        for book_row in books_to_process:
            # Create BookState from database row
            book_state = BookState.from_db_row(book_row, CONFIG)

            # Check processing delay (once per book, not per directory!)
            # Legacy-compatible: processes even if Completed==0 (some clients don't set it)
            if processing_delay:
                should_delay, elapsed = book_state.should_delay_processing(
                    processing_delay
                )
                if should_delay:
                    postprocesslogger.warning(
                        f"Ignoring {book_state.download_title} as completion was only {elapsed} "
                        f"{plural(elapsed, 'second')} ago, delay is {processing_delay}"
                    )
                    continue
                # Only log completion time if we have a valid timestamp
                if book_state.is_completed():
                    postprocesslogger.debug(
                        f"{book_state.download_title} was completed {elapsed} {plural(elapsed, 'second')} ago"
                    )
                else:
                    postprocesslogger.debug(
                        f"{book_state.download_title} has no completion timestamp (client doesn't support it)"
                    )

            # Enrich with download_folder (general + name) and book_title
            book_state.enrich_with_download_info(db)

            postprocesslogger.debug(
                f"Enrichment result: download_folder='{book_state.download_folder}', "
                f"book_title='{book_state.book_title[:50] if book_state.book_title else '(empty)'}'"
            )

            # ========================================================
            #  Downloaded Processing Stage | Targeted Location Pass
            # ========================================================
            if book_state.download_folder:
                postprocesslogger.debug(
                    f"Targeted Location Pass: Processing search for {book_state.download_title}"
                )
                result = _search_in_known_location(
                    book_state, ignoreclient, db, postprocesslogger, fuzzlogger
                )
                if result > 0:
                    ppcount += result
                    postprocesslogger.info(
                        f"Matching Stage - First Pass SUCCESS: {book_state.download_title}"
                    )
                    continue  # Successfully processed, move to next book

                postprocesslogger.debug(
                    f"Matching Stage - First Pass unsuccessful for {book_state.download_title}, trying Pass 2"
                )
            else:
                postprocesslogger.debug(
                    f"Matching Stage - First Pass skipped because no targeted download folder "
                    f"specified for {book_state.download_title}, trying Pass 2"
                )

            # =======================================================
            #  Matching Stage | Second Pass: Fuzzy Search
            #  * Only here if not processed in First Pass
            # =======================================================
            postprocesslogger.debug(
                f"Matching Stage - Second Pass: Fallback search for {book_state.download_title}"
            )
            result = _process_snatched_book(
                book_state,
                all_downloads,
                ignoreclient,
                db,
                postprocesslogger,
                fuzzlogger,
            )
            if result > 0:
                ppcount += result
                postprocesslogger.info(
                    f"Matching Stage - Second Pass SUCCESS: {book_state.download_title}"
                )
            else:
                postprocesslogger.warning(
                    f"Matching Stage - Second Pass FAILED: {book_state.download_title}. No matches"
                )

        postprocesslogger.debug("Snatched Processing Stage Complete")

        # ==========================================================================
        #  Supplemental Search: Look for and process books in LL.(bookid) Folders
        # ==========================================================================
        postprocesslogger.info("Supplemental Search: Processing LL.(bookid) folders")
        if all_downloads:
            ppcount += _process_ll_bookid_folders_from_list(
                all_downloads, db, postprocesslogger
            )

        # Mark unprocessed books as Failed to force a retry on next search
        failed = [0, 0, 0, 0]  # ebook, audio, mag, comic
        for book_row in books_to_process:
            book_state = BookState.from_db_row(book_row, CONFIG)
            if not book_state.was_processed and book_state.has_failed():
                logger.warning(
                    f"Marking {book_state.download_title} as Failed: "
                    f"{book_state.processing_stage} - {book_state.failure_reason}"
                )
                control_value_dict = {"DownloadID": book_state.download_id, "Status": "Snatched"}
                new_value_dict = {
                    "Status": "Failed",
                    "NZBDate": now(),
                    "DLResult": f"{book_state.processing_stage}: {book_state.failure_reason}"
                }
                db.upsert("wanted", new_value_dict, control_value_dict)
                book_type_str = book_state.get_book_type_str()
                cmd = ''
                if book_type_str == BookType.EBOOK.value:
                    cmd = "UPDATE books SET status='Wanted' WHERE status='Snatched' and BookID=?"
                    failed[0] += 1
                elif book_type_str == BookType.AUDIOBOOK.value:
                    cmd = "UPDATE books SET audiostatus='Wanted' WHERE audiostatus='Snatched' and BookID=?"
                    failed[1] += 1
                elif book_type_str == BookType.MAGAZINE.value():
                    failed[2] += 1
                elif book_type_str == BookType.COMIC.value():
                    failed[3] += 1
                if cmd:
                    db.action(cmd, (book_state.book_id,))
                if CONFIG.get_bool("DEL_FAILED"):
                    delete_task(book_state.source, book_state.download_id, True)
        if any(failed):
            logger.debug(f"Failed to process {failed[0]} ebook, {failed[1]} audio, {failed[2]} magazine, {failed[3]} comic")

        postprocesslogger.info(f"{ppcount} {plural(ppcount, 'download')} processed.")

        # ═══════════════════════════════════════════════════════════
        # PASS 3: DOWNLOAD STATUS MANAGEMENT
        # ═══════════════════════════════════════════════════════════
        postprocesslogger.info("Third pass: Download status management")

        _manage_download_status(db, postprocesslogger)

        # Cleanup and scheduling
        logger.debug(f"Storing finish time for {thread_name()}")
        db.upsert("jobs", {"Finish": time.time()}, {"Name": thread_name()})
        _check_and_schedule_next_run(db, logger, reset)

    except Exception:
        logger.error(f"Unhandled exception in process_dir: {traceback.format_exc()}")
    finally:
        db.close()
        # Restore original thread name
        thread_name(incoming_threadname)


def _process_ll_bookid_folders_from_list(all_downloads, db, logger):
    """
    Process unsnatched books/audiobooks with LL.(bookid) naming from compiled list.

    Searches through compiled download list for items named "LL.(bookid)".
    These are books that weren't explicitly snatched but appeared in downloads.

    Args:
        all_downloads: List of (parent_dir, filename) tuples
        db: Database connection
        logger: Logger instance

    Returns:
        Count of books successfully processed
    """
    ppcount = 0
    skipped_extensions = get_list(CONFIG["SKIPPED_EXT"])
    TELEMETRY.record_usage_data("Process/Residual")

    logger.debug(f"Scanning {len(all_downloads)} items for LL.(bookid) pattern")

    for parent_dir, _entry in all_downloads:
        entry = enforce_str(_entry)
        if "LL.(" in entry:
            _, extn = os.path.splitext(entry)
            if not extn or extn.strip(".") not in skipped_extensions:
                book_id = entry.split("LL.(")[1].split(")")[0]
                logger.debug(f"Book with id: {book_id} found in {parent_dir}")
                book_path = os.path.join(parent_dir, entry)

                # At this point we don't know if we want audio or ebook or both
                is_audio = book_file(book_path, "audiobook", config=CONFIG) != ""
                is_ebook = book_file(book_path, "ebook", config=CONFIG) != ""
                logger.debug(f"Contains ebook={is_ebook} audio={is_audio}")

                data = db.match(
                    "SELECT BookFile,AudioFile from books WHERE BookID=?",
                    (book_id,),
                )
                have_ebook = data and data["BookFile"] and path_isfile(data["BookFile"])
                have_audio = (
                    data and data["AudioFile"] and path_isfile(data["AudioFile"])
                )
                logger.debug(f"Already have ebook={have_ebook} audio={have_audio}")

                if (have_ebook and have_audio) or (
                    have_ebook and not CONFIG.get_bool("AUDIO_TAB")
                ):
                    exists = True
                else:
                    exists = False

                if exists:
                    logger.debug(f"Skipping BookID {book_id}, already exists")
                else:
                    logger.debug(f"Checking type of {book_path}")

                    if path_isfile(book_path):
                        logger.debug(f"{book_path} is a file")
                        # We want to work on the download directory not the individual file
                        book_path = os.path.normpath(parent_dir)

                    if path_isdir(book_path):
                        logger.debug(f"{book_path} is a dir")
                        if process_book(book_path, book_id, logger=logger):
                            logger.debug(f"Imported {book_path}")
                            ppcount += 1
            else:
                logger.debug(f"Skipping extn {entry}")
        else:
            logger.debug(f"Skipping (no LL bookid) {entry}")

    return ppcount


def process_book(book_path: str, book_id: str, logger=None, library=""):
    TELEMETRY.record_usage_data("Process/Book")
    if not logger:
        logger = logging.getLogger(__name__)

    db = database.DBConnection()
    # noinspection PyBroadException
    try:
        # Move a book into LL folder structure given just the folder and bookID, returns True or False
        # Called from "import_alternate" or if we find a "LL.(xxx)" folder that doesn't match a snatched book/mag
        logger.debug(f"process_book {book_path}")
        is_audio = book_file(book_path, "audiobook", config=CONFIG) != ""
        is_ebook = book_file(book_path, "ebook", config=CONFIG) != ""

        # Get the details of the book
        cmd = (
            "SELECT AuthorName,BookName,BookID,books.Status,AudioStatus from books,authors WHERE BookID=? "
            "and books.AuthorID = authors.AuthorID"
        )
        data = dict(db.match(cmd, (book_id,)))
        if data:
            authorname = data["AuthorName"]
            bookname = data["BookName"]
            want_audio = False
            want_ebook = False
            book_type_enum = None

            if data["Status"] in ["Wanted", "Snatched"] or library == "eBook":
                want_ebook = True
            if data["AudioStatus"] in ["Wanted", "Snatched"] or library == "Audio":
                want_audio = True

            # we may have wanted to snatch an ebook and audiobook of the same title/id
            cmd = "SELECT BookID, NZBprov, NZBmode,AuxInfo FROM wanted WHERE BookID=? and Status='Snatched'"
            was_snatched = db.select(cmd, (book_id,))

            # For each snatched type of a book id, see if
            # it is an ebook or audiobook
            for item in was_snatched:
                book_type_enum = BookType.from_string(
                    item["AuxInfo"] or "ebook"
                )  # default to ebook if unknown
                if book_type_enum == BookType.AUDIOBOOK:
                    want_audio = True
                elif book_type_enum == BookType.EBOOK:
                    want_ebook = True

            if not is_audio and not is_ebook:
                logger.debug(f"Bookid {book_id}, failed to find valid booktype")
            elif want_audio and is_audio:
                book_type_enum = BookType.AUDIOBOOK
            elif want_ebook and is_ebook:
                book_type_enum = BookType.EBOOK
            elif not was_snatched:
                logger.debug(
                    f"Bookid {book_id} was not snatched so cannot check type, contains ebook:{is_ebook} "
                    f"audio:{is_audio}"
                )

                # If audiobooks aren't enable, don't look for it
                if not CONFIG.get_bool("AUDIO_TAB"):
                    is_audio = False

                if is_audio:
                    book_type_enum = BookType.AUDIOBOOK
                elif is_ebook:
                    book_type_enum = BookType.EBOOK

            if book_type_enum == BookType.AUDIOBOOK:
                dest_dir = enforce_str(
                    str(get_directory("Audio"))
                )  # Ensure string for join
            elif book_type_enum == BookType.EBOOK:
                dest_dir = enforce_str(
                    str(get_directory("eBook"))
                )  # Ensure string for join
            else:
                logger.debug(
                    f"Bookid {book_id}, failed to find valid booktype, contains ebook:{is_ebook} audio:{is_audio}"
                )
                return False

            namevars = name_vars(book_id)
            # global_name is only used for ebooks to ensure book/cover/opf all have the same basename
            # audiobooks are usually multipart so can't be renamed this way
            global_name = str(namevars["BookFile"])  # Enforce string from dict
            if book_type_enum == BookType.AUDIOBOOK:
                audio_folder = str(
                    namevars["AudioFolderName"]
                )  # Enforce string from dict
                dest_path = stripspaces(os.path.join(dest_dir, audio_folder))
            else:
                folder_name = str(namevars["FolderName"])  # Enforce string from dict
                dest_path = stripspaces(os.path.join(dest_dir, folder_name))

            # Validate encoding via make_utf8bytes, then decode to string for metadata
            dest_path = enforce_str(enforce_bytes(dest_path))

            # Create metadata object for manual processing
            # For manual processing, we have limited metadata available
            metadata = EbookMetadata(
                book_id=book_id,
                book_type_enum=book_type_enum,
                dest_path=dest_path,
                global_name=global_name,
                author_name=authorname,
                book_name=bookname,
            )

            success, dest_file, book_path = _process_destination(
                book_metadata=metadata,
                book_path=book_path,
                logger=logger,
                # No mode for manual processing
            )
            book_type_aux = (
                "AudioBook" if (book_type_enum == BookType.AUDIOBOOK) else "eBook"
            )
            if success:
                # update nzbs
                dest_file = enforce_str(make_unicode(dest_file))
                control_value_dict = {"BookID": book_id}
                if was_snatched:
                    snatched_from = CONFIG.disp_name(was_snatched[0]["NZBprov"])
                    logger.debug(f"{global_name} was snatched from {snatched_from}")
                    new_value_dict = {
                        "Status": "Processed",
                        "NZBDate": now(),
                        "DLResult": dest_file,
                    }
                    db.upsert("wanted", new_value_dict, control_value_dict)
                else:
                    new_value_dict = {
                        "Status": "Processed",
                        "NZBProv": "Manual",
                        "AuxInfo": book_type_aux,
                        "NZBDate": now(),
                        "DLResult": dest_file,
                        "NZBSize": 0,
                    }
                    if path_isfile(dest_file):
                        new_value_dict["NZBSize"] = os.path.getsize(syspath(dest_file))

                    db.upsert("wanted", new_value_dict, control_value_dict)
                    snatched_from = "manually added"
                    logger.debug(f"{book_type_aux} {global_name} was {snatched_from}")

                if dest_file:  # do we know the location (not calibre already exists)
                    _process_extras(
                        dest_file, global_name, book_id, book_type_enum.value
                    )

                if ".unpack" in book_path:
                    book_path = f"{book_path.split('.unpack')[0]}.unpack"

                if (
                    ".unpack" in book_path
                    or (not CONFIG.get_bool("DESTINATION_COPY")
                        and book_path != dest_dir)
                ):
                    if path_isdir(book_path):
                        # calibre might have already deleted it?
                        logger.debug(f"Deleting {book_path}")
                        shutil.rmtree(book_path, ignore_errors=True)
                elif CONFIG.get_bool("DESTINATION_COPY"):
                    logger.debug(f"Not removing {book_path} as Keep Files is set")
                else:
                    logger.debug(f"Not removing {book_path} as in download root")

                logger.info(f"Successfully processed: {global_name}")
                custom_notify_download(f"{book_id} {book_type_aux}")
                frm = "" if snatched_from == "manually added" else "from "

                notify_download(
                    f"{book_type_aux} {global_name} {frm}{snatched_from} at {now()}",
                    book_id,
                )
                mailing_list(book_type_aux, global_name, book_id)
                if was_snatched:
                    _update_downloads_provider_count(
                        CONFIG.disp_name(was_snatched[0]["NZBprov"])
                    )
                else:
                    _update_downloads_provider_count("manually added")
                return True
            logger.error(
                f"Postprocessing for {global_name!r} has failed: {dest_file!r}. Deleting {book_path}.fail"
            )
            shutil.rmtree(f"{book_path}.fail", ignore_errors=True)
            try:
                _ = safe_move(book_path, f"{book_path}.fail")
                logger.warning(f"Residual files remain in {book_path}.fail")
            except Exception as e:
                logger.error(
                    f"Unable to rename {book_path!r}, {type(e).__name__} {e!s}"
                )
                if not os.access(syspath(book_path), os.R_OK):
                    logger.error(f"{book_path!r} is not readable")
                if not os.access(syspath(book_path), os.W_OK):
                    logger.error(f"{book_path!r} is not writeable")
                parent = os.path.dirname(book_path)
                try:
                    with open(
                        syspath(os.path.join(parent, "ll_temp")),
                        "w",
                        encoding="utf-8",
                    ) as f:
                        f.write("test")
                    remove_file(os.path.join(parent, "ll_temp"))
                except Exception as why:
                    logger.error(f"Directory {parent} is not writeable: {why}")
                logger.warning(f"Residual files remain in {book_path}")

            was_snatched = dict(
                db.match(
                    "SELECT NZBurl FROM wanted WHERE BookID=? and Status='Snatched'",
                    (book_id,),
                )
            )
            if was_snatched:
                control_value_dict = {"NZBurl": was_snatched["NZBurl"]}
                new_value_dict = {
                    "Status": "Failed",
                    "NZBDate": now(),
                    "DLResult": dest_file,
                }
                db.upsert("wanted", new_value_dict, control_value_dict)
            # reset status so we try for a different version
            if book_type_enum == BookType.AUDIOBOOK:
                db.action(
                    "UPDATE books SET audiostatus='Wanted' WHERE BookID=?",
                    (book_id,),
                )
            else:
                db.action(
                    "UPDATE books SET status='Wanted' WHERE BookID=?", (book_id,)
                )
        return False
    except Exception:
        logger.error(f"Unhandled exception in process_book: {traceback.format_exc()}")
        return False
    finally:
        db.close()


def _process_extras(
    dest_file=None, global_name=None, bookid=None, book_type: str = BookType.EBOOK.value
):
    # given bookid, handle author count, calibre autoadd, book image, opf

    logger = logging.getLogger(__name__)
    if not bookid:
        logger.error("No bookid supplied")
        return
    if not dest_file:
        logger.error("No dest_file supplied")
        return

    TELEMETRY.record_usage_data("Process/Extras")
    db = database.DBConnection()
    try:
        booktype_enum = BookType.from_string(book_type)
        control_value_dict = {"BookID": bookid}
        if booktype_enum == BookType.AUDIOBOOK:
            new_value_dict = {
                "AudioFile": dest_file,
                "AudioStatus": CONFIG["FOUND_STATUS"],
                "AudioLibrary": now(),
            }
            db.upsert("books", new_value_dict, control_value_dict)
            if CONFIG["AUDIOBOOK_DEST_FILE"]:
                book_filename = audio_rename(bookid, rename=True, playlist=True)
                if dest_file != book_filename:
                    db.action(
                        "UPDATE books set AudioFile=? where BookID=?",
                        (book_filename, bookid),
                    )
        else:
            new_value_dict = {
                "Status": CONFIG["FOUND_STATUS"],
                "BookFile": dest_file,
                "BookLibrary": now(),
            }
            db.upsert("books", new_value_dict, control_value_dict)

        # update authors book counts
        match = dict(db.match("SELECT AuthorID FROM books WHERE BookID=?", (bookid,)))
        if match:
            update_totals(match["AuthorID"])

        elif booktype_enum != BookType.EBOOK:  # only do autoadd/img/opf for ebooks
            return

        cmd = (
            "SELECT AuthorName,BookID,BookName,BookDesc,BookIsbn,BookImg,BookDate,BookLang,BookPub,BookRate,"
            "Narrator from books,authors WHERE BookID=? and books.AuthorID = authors.AuthorID"
        )
        data = dict(db.match(cmd, (bookid,)))
        if not data:
            logger.error(f"No data found for bookid {bookid}")
            return
    finally:
        db.close()

    dest_path = os.path.dirname(dest_file)

    # download and cache image if http link
    process_img(dest_path, data["BookID"], data["BookImg"], global_name, ImageType.BOOK)

    # do we want to create metadata - there may already be one in book_path, but it was downloaded and might
    # not contain our choice of authorname/title/identifier, so if autoadding we ignore it and write our own
    if not CONFIG.get_bool("IMP_AUTOADD_BOOKONLY"):
        _ = create_opf(dest_path, data, global_name, overwrite=True)
    else:
        _ = create_opf(dest_path, data, global_name, overwrite=False)
    # if our_opf:
    #     write_meta(dest_path, opf_file)  # write metadata from opf to all ebook types in dest folder

    # If you use auto add by Calibre you need the book in a single directory, not nested
    # So take the files you Copied/Moved to Dest_path and copy/move into Calibre auto add folder.
    if CONFIG["IMP_AUTOADD"]:
        _process_auto_add(dest_path)


def _find_best_format(
    path: str, prioritized_list: "list[str]"
) -> "tuple[str, set[str]]":
    dir_list = listdir(path)
    found_set: set[str] = set()

    # Collect all valid extension types in a set
    for _fname in dir_list:
        fname = enforce_str(_fname)  # Ensure string for path operations
        _, extn = _tokenize_file(fname)
        extn = extn.lower()
        if extn in prioritized_list:
            found_set.add(extn)

    best_match = ""
    # Now pick the best found type based on the order in the prioritized extn list
    for extn in prioritized_list:
        if extn in found_set:
            best_match = extn
            break

    return best_match, found_set


def _is_metadata_file(fname: str) -> bool:
    """Check if file is a metadata file (.jpg or .opf)"""
    fname_lower = fname.lower()
    return fname_lower.endswith((".jpg", ".opf"))


def _should_use_calibre(book_type: str) -> bool:
    """Determine if Calibre should be used for this book type"""
    if not CONFIG["IMP_CALIBREDB"]:
        return False

    calibre_settings = {
        "ebook": "IMP_CALIBRE_EBOOK",
        "magazine": "IMP_CALIBRE_MAGAZINE",
        "comic": "IMP_CALIBRE_COMIC",
    }

    setting = calibre_settings.get(book_type)
    if setting:
        return CONFIG.get_bool(setting)

    return False


def _prepare_destination_directory(dest_path, logger) -> "tuple[bool, str, bytes]":
    """
    Ensure destination directory exists and is ready for file operations.

    Matches original postprocess.py pattern: keeps dest_path as bytes for
    filesystem operations (encoding-safe across platforms).

    Args:
        dest_path: Destination directory path
        logger: Logger instance

    Returns:
        Tuple of (success, error_message, dest_path_bytes)
        - success: True if directory is ready, False on error
        - error_message: Error description if success is False, empty string otherwise
        - dest_path_bytes: UTF8-encoded destination path as bytes (for filesystem ops)
    """

    if not path_exists(dest_path):
        logger.debug(f"{dest_path} does not exist, so it's safe to create it")
    elif not path_isdir(dest_path):
        logger.debug(f"{dest_path} exists but is not a directory, deleting it")
        try:
            remove_file(dest_path)
        except OSError as why:
            return False, f"Unable to delete {dest_path}: {why.strerror}", dest_path

    if path_isdir(dest_path):
        setperm(dest_path)
    elif not make_dirs(dest_path):
        return False, f"Unable to create directory {dest_path}", dest_path

    # Note: encoding detection is handled inside enforce_bytes via make_utf8bytes
    dest_path = enforce_bytes(dest_path)  # Convert to bytes and enforce type

    return True, "", dest_path


def _should_copy_file(fname: str, best_format: str, book_type: str) -> bool:
    """
    Determine if a file should be copied to the destination.

    Args:
        fname: Filename to check
        best_format: Best format to keep (empty string if not filtering)
        book_type: Type of book (ebook, audiobook, comic, magazine)

    Returns:
        True if file should be copied, False otherwise
    """
    # If we're filtering for a specific format and this is a valid booktype
    # but not the best format, skip it
    if best_format and CONFIG.is_valid_booktype(fname, booktype=book_type) and not fname.endswith(best_format):
        return False

    # Copy valid book files or metadata files
    return CONFIG.is_valid_booktype(fname, booktype=book_type) or _is_metadata_file(
        fname
    )


def _get_dest_filename(
    fname: str, global_name: str, book_type: str, dest_dir: str
) -> str:
    """
    Generate destination filename based on book type.

    Args:
        fname: Source filename
        global_name: Base name for renamed files
        book_type: Type of book (ebook, audiobook, comic, magazine)
        dest_dir: Destination directory

    Returns:
        Full destination file path
    """
    if book_type in [BookType.AUDIOBOOK.value, BookType.COMIC.value]:
        # For audiobooks and comics, only rename metadata files
        if _is_metadata_file(fname):
            return os.path.join(dest_dir, global_name + os.path.splitext(fname)[1])
        # Keep original filename for audio/comic files
        return os.path.join(dest_dir, fname)
    # For ebooks and magazines, rename all files
    return os.path.join(dest_dir, global_name + os.path.splitext(fname)[1])


def _find_preferred_book_file(
    book_type: str,
    dest_path: bytes,
    global_name: bytes,
    dir_list,
    udest_path: str,
    logger,
) -> str:
    """
    Find the preferred file to use as the main book file.

    For ebooks: Find the first format matching the priority order
    For audiobooks: Find first part of multipart or whole-book file
    For other types: Return empty string

    Args:
        book_type: Type of book (ebook, audiobook, etc.)
        dest_path: Destination path (bytes)
        global_name: Global filename (bytes)
        dir_list: List of files in source directory
        udest_path: Unicode destination path
        logger: Logger instance

    Returns:
        Path to preferred file, or empty string if none found
    """
    if book_type == BookType.EBOOK.value:
        book_basename = os.path.join(dest_path, global_name)
        book_basename_str = enforce_str(
            make_unicode(book_basename)
        )  # Ensure string for f-string
        ebook_extn_list = get_list(CONFIG["EBOOK_TYPE"])
        for extn in ebook_extn_list:
            preferred_type = f"{book_basename_str}.{extn}"
            if path_exists(preferred_type):
                logger.debug(f"Link to preferred type {extn}, {preferred_type}")
                return preferred_type
        return ""

    if book_type == BookType.AUDIOBOOK.value:
        firstfile = ""
        tokmatch = ""

        # First, look for a whole-book file (no numbers in filename)
        for f in dir_list:
            if not re.findall(r"\d+\b", f) and CONFIG.is_valid_booktype(
                f, booktype=book_type
            ):
                firstfile = os.path.join(udest_path, f)
                tokmatch = "whole"
                logger.debug(f"Found whole audiobook file: {f}")
                break

        # If no whole-book file, find first part by common numbering patterns
        if not tokmatch:
            for token in [" 001.", " 01.", " 1.", " 001 ", " 01 ", " 1 ", "001", "01"]:
                if tokmatch:
                    break
                for f in dir_list:
                    if CONFIG.is_valid_booktype(f, booktype=book_type):
                        if not firstfile:
                            firstfile = os.path.join(udest_path, f)
                            logger.debug(f"Primary link to {f}")
                        if token in f:
                            firstfile = os.path.join(udest_path, f)
                            logger.debug(f"Link to first part [{token}], {f}")
                            tokmatch = token
                            break

        return firstfile

    return ""


def _handle_magazine_comic_metadata(
    book_type: str,
    book_path: str,
    book_filename: str,
    bookid: str,
    issueid: str,
    title: str,
    issuedate: str,
    mag_genres: str,
    global_name,
    udest_path: str,
    logger,
):
    """
    Create metadata files and .ll_ignore for magazines and comics.

    Args:
        book_type: Type (comic or magazine)
        book_path: Path to book files
        bookid: Book ID
        issueid: Issue ID (for comics/magazines)
        title: Magazine title
        issuedate: Issue date
        mag_genres: Magazine genres
        global_name: Global filename
        udest_path: Unicode destination path
        logger: Logger instance
    """
    # Create .ll_ignore file
    try:
        ignorefile = os.path.join(udest_path, ".ll_ignore")
        with open(syspath(ignorefile), "w") as f:
            f.write(book_type)
    except (OSError, TypeError) as e:
        logger.warning(f"Unable to create/write to ignorefile: {e!s}")

    if book_type == BookType.COMIC.value:
        cmd = (
            "SELECT Title,comicissues.ComicID,IssueID,IssueAcquired,IssueFile,comicissues.Cover,"
            "Publisher,Contributors from comics,comicissues WHERE "
            "comics.ComicID = comicissues.ComicID and IssueID=? and comicissues.ComicID=?"
        )
        db = database.DBConnection()
        try:
            data = dict(db.match(cmd, (issueid, bookid)))
        finally:
            db.close()

        bookid = f"{bookid}_{issueid}"
        if data:
            # process_img and create_comic_opf expect string for global_name
            process_img(book_path, bookid, data["Cover"], global_name, ImageType.COMIC)
            if CONFIG.get_bool("IMP_COMICOPF"):
                _, _ = create_comic_opf(book_path, data, global_name, True)
            else:
                logger.debug("create_comic_opf is disabled")
        else:
            logger.debug(f"No data found for {bookid}")

    elif CONFIG.get_bool("IMP_MAGOPF"):
        db = database.DBConnection()
        try:
            entry = dict(
                db.match(
                    "SELECT Language,Genre FROM magazines where Title=? COLLATE NOCASE",
                    (title,),
                )
            )
            if entry:
                _, _ = create_mag_opf(
                    book_filename,
                    title,
                    issuedate,
                    issueid,
                    language=entry["Language"],
                    genres=mag_genres,
                    overwrite=True,
                )
        finally:
            db.close()
    else:
        logger.debug("create_mag_opf is disabled")


def _process_destination(
    book_metadata: BookMetadata,
    book_path: str,
    logger: logging.Logger,
    mode: str = "",
    preprocess: bool = True,
) -> "tuple[bool, str, str ]":
    """
    Copy/move book/mag and associated files into target directory.

    Args:
        book_path: Source path containing the book files
        book_metadata: BookMetadata object with all book information and destination paths
        mode: Download mode (torrent, magnet, nzb, etc.) for copy/seeding logic
        preprocess: Whether to run preprocessing steps

    Returns:
        Tuple of (success, full_path_to_book, book_path)
        - success: True if successful, False otherwise
        - full_path_to_book: Path to the processed book file
        - book_path: Processing path (may have changed to .unpack)
    """
    TELEMETRY.record_usage_data("Process/Destination")

    # Extract commonly used fields from metadata
    book_type = book_metadata.book_type
    book_id = book_metadata.book_id
    dest_path = book_metadata.dest_path

    # Convert global_name to bytes for filesystem operations
    # Create unicode version for use in os.path.join and function calls
    global_name = enforce_bytes(book_metadata.global_name)
    uglobal_name = enforce_str(global_name)  # Convert bytes to string for joins

    # Get type-specific fields from metadata
    fields = book_metadata.get_processing_fields()
    authorname = fields["authorname"]
    bookname = fields["bookname"]
    issueid = fields["issueid"]
    title = fields["title"]
    issuedate = fields["issuedate"]
    mag_genres = fields["mag_genres"]
    cover = fields["cover"]

    book_path = enforce_str(make_unicode(book_path))

    logger.info(
        f"DESTINATION PROCESSING - Book type: {book_type}, Source: {book_path}, "
        f"Destination: {dest_path}, Global name: {uglobal_name}"
    )

    dir_list = listdir(book_path)
    best_format = ""
    found_types = {}
    single_ebook_type = book_type == BookType.EBOOK.value and CONFIG.get_bool(
        "ONE_FORMAT"
    )
    if single_ebook_type:
        ebook_extn_list = get_list(CONFIG["EBOOK_TYPE"])
        best_format, found_types = _find_best_format(book_path, ebook_extn_list)

    match = False
    if best_format:
        match = True
        logger.debug(
            f"One format import, found {','.join(found_types)}, best match {best_format}"
        )
    else:  # mag, comic or audiobook or multi-format book
        for _fname in dir_list:
            fname = enforce_str(_fname)
            if CONFIG.is_valid_booktype(fname, booktype=book_type):
                match = True
                break

    if not match:
        # no book/mag found in a format we wanted. Leave for the user to delete or convert manually
        return (
            False,
            f"Unable to locate a valid filetype ({book_type}) in {book_path}, leaving for manual processing",
            book_path,
        )

    _, path_extn = _tokenize_file(book_path)
    if path_extn != "unpack" and (
        CONFIG.get_bool("DESTINATION_COPY")
        or (
            mode in ["torrent", "magnet", "torznab"] and CONFIG.get_bool("KEEP_SEEDING")
        )
    ):
        dest_dir = f"{book_path}.unpack"
        logger.debug(f"Copying to target {dest_dir}")
        failed, err = copy_tree(book_path, dest_dir)
        if not failed:
            book_path = dest_dir
        else:
            msg = f"Failed to copy {failed} files to {dest_dir}, aborted"
            logger.error(msg)
            logger.debug(f"{err}")
            return False, msg, ""

    if preprocess:
        logger.debug(f"PreProcess ({book_type}) {book_path}")
        if book_type == BookType.EBOOK.value:
            preprocess_ebook(book_path)
        elif book_type == BookType.AUDIOBOOK.value:
            preprocess_audio(book_path, book_id, authorname, bookname)
        elif book_type == BookType.MAGAZINE.value:
            # Use metadata fields instead of querying database again
            success, msg = preprocess_magazine(
                book_path,
                cover=cover,
                tag=CONFIG.get_bool("TAG_PDF"),
                title=book_id,
                issue=issuedate,
                genres=mag_genres,
            )
            if not success:
                return False, msg, book_path

        # run custom pre-processing, for example remove unwanted formats
        # or force format conversion before sending to calibre
        if len(CONFIG["EXT_PREPROCESS"]):
            logger.debug(
                f"Running external PreProcessor: {book_type} {book_path} {authorname} {bookname}"
            )
            params = [
                CONFIG["EXT_PREPROCESS"],
                book_type,
                book_path,
                authorname,
                bookname,
            ]
            rc, res, err = run_script(params)
            if rc:
                return (
                    False,
                    f"PreProcessor returned {rc}: res[{res}] err[{err}]",
                    book_path,
                )
            logger.debug(f"PreProcessor: {res}", book_path)

        if single_ebook_type:
            ebook_extn_list = get_list(CONFIG["EBOOK_TYPE"])
            best_format, found_types = _find_best_format(book_path, ebook_extn_list)
            logger.debug(
                f"After PreProcessing, found {','.join(found_types)}, best match {best_format}"
            )

    # If ebook, magazine or comic, do we want calibre to import it for us
    newbookfile = ""
    if _should_use_calibre(book_type):
        # Build data dict for Calibre from metadata
        data = book_metadata.get_opf_data()
        data["bestformat"] = best_format
        data["cover"] = cover
        data["mag_genres"] = mag_genres
        return send_to_calibre(book_type, uglobal_name, book_path, data)

    # we are copying the files ourselves
    success, error_msg, dest_path = _prepare_destination_directory(dest_path, logger)
    if not success:
        logger.error(f"FAIL: Cannot prepare destination: {error_msg}")
        return False, error_msg, book_path

    # dest_path is bytes - create unicode version for string operations
    udest_path = enforce_str(dest_path)  # Convert bytes to string for os.path.join

    dir_list = listdir(book_path)  # Refresh our directory listing
    copied_count = 0

    # ok, we've got a target directory, try to copy only the files we want, renaming them on the fly.
    logger.info(f"COPY FILES STARTING - {book_path} ==> {udest_path}")
    for _fname in dir_list:
        fname = enforce_str(_fname)
        if not _should_copy_file(fname, best_format, book_type):
            logger.debug(f"Skip: {fname}")
            continue

        srcfile = os.path.join(book_path, fname)
        destfile = _get_dest_filename(fname, uglobal_name, book_type, udest_path)

        try:
            destfile = safe_copy(srcfile, destfile)
            setperm(destfile)
            copied_count += 1
            logger.info(f"File copied to {destfile}")
            if destfile and CONFIG.is_valid_booktype(
                enforce_str(make_unicode(destfile)), booktype=book_type
            ):
                newbookfile = destfile
        except Exception as why:
            # extra debugging to see if we can figure out a windows encoding issue
            parent = os.path.dirname(destfile)
            try:
                with open(
                    syspath(os.path.join(parent, "ll_temp")),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write("test")
                remove_file(os.path.join(parent, "ll_temp"))
            except Exception as w:
                logger.error(f"Destination Directory [{parent}] is not writeable: {w}")
            return (
                False,
                f"Unable to copy file {srcfile} to {destfile}: {type(why).__name__} {why!s}",
                book_path,
            )

    logger.info(f"COPY FILES COMPLETE - Files copied: {copied_count} to {udest_path}")

    if book_type in [BookType.EBOOK.value, BookType.AUDIOBOOK.value]:
        # Use metadata we already have instead of querying database again
        # Use isinstance for type narrowing (pyright requires this)
        if isinstance(book_metadata, EbookMetadata):
            process_img(
                book_path,
                book_id,
                book_metadata.book_img,
                uglobal_name,
                ImageType.BOOK,
            )
            opf_data = book_metadata.get_opf_data()
            _ = create_opf(book_path, opf_data, uglobal_name, True)

        # try to keep track of "preferred" ebook type or the first part of multipart audiobooks
        # Find the preferred file to use as the main book file
        firstfile = _find_preferred_book_file(
            book_type, dest_path, global_name, dir_list, udest_path, logger
        )

        if firstfile:
            newbookfile = firstfile
            logger.info(f"Primary book file: {newbookfile}")

    # Handle magazine/comic metadata creation
    elif book_type in [BookType.MAGAZINE.value, BookType.COMIC.value]:
        _handle_magazine_comic_metadata(
            book_type,
            book_path,
            newbookfile,
            book_id,
            issueid,
            title,
            issuedate,
            mag_genres,
            uglobal_name,  # Use unicode version for process_img
            udest_path,
            logger,
        )

    logger.info(f"DESTINATION PROCESSING COMPLETE - {uglobal_name}")
    return True, newbookfile, book_path


def _process_auto_add(src_path: str, book_type_enum: BookType = BookType.EBOOK):
    # Called to copy/move the book files to an auto add directory for the likes of Calibre which can't do nested dirs
    logger = logging.getLogger(__name__)
    autoadddir = CONFIG["IMP_AUTOADD"]
    savefiles = CONFIG.get_bool("IMP_AUTOADD_COPY")

    book_type_str = book_type_enum.value
    if book_type_enum == BookType.MAGAZINE:
        autoadddir = CONFIG["IMP_AUTOADDMAG"]
        savefiles = CONFIG.get_bool("IMP_AUTOADDMAG_COPY")

    if not path_exists(autoadddir):
        logger.error(
            f"AutoAdd directory for {book_type_str} [{autoadddir}] is missing or not set - cannot perform autoadd"
        )
        return False
    TELEMETRY.record_usage_data("Process/Autoadd")
    # Now try and copy all the book files into a single dir.
    try:
        names = listdir(src_path)
        # files jpg, opf & book(s) should have same name
        # Caution - book may be pdf, mobi, epub or all 3.
        # for now simply copy all files, and let the autoadder sort it out
        #
        # Update - seems Calibre will only use the jpeg if named same as book, not cover.jpg
        # and only imports one format of each ebook, treats the others as duplicates, might be configable in calibre?
        # ignores author/title data in opf file if there is any embedded in book

        match = False
        if book_type_enum == BookType.EBOOK and CONFIG.get_bool("ONE_FORMAT"):
            booktype_list = get_list(CONFIG["EBOOK_TYPE"])
            for bktype in booktype_list:
                while not match:
                    for _name in names:
                        name = enforce_str(_name)
                        extn = os.path.splitext(name)[1].lstrip(".")
                        if extn and extn.lower() == bktype:
                            match = bktype
                            break
        copied = False
        for _name in names:
            name = enforce_str(_name)
            valid_type = CONFIG.is_valid_booktype(name, book_type_str)
            if match and valid_type and not name.endswith(match):
                logger.debug(f"Skipping book format {os.path.splitext(name)[1]}")
            elif (
                book_type_enum == BookType.EBOOK
                and CONFIG.get_bool("IMP_AUTOADD_BOOKONLY")
                and not valid_type
            ):
                logger.debug(f"Skipping non-book {name}")
            elif (
                book_type_enum == BookType.MAGAZINE
                and CONFIG.get_bool("IMP_AUTOADD_MAGONLY")
                and not valid_type
            ):
                logger.debug(f"Skipping non-mag {name}")
            else:
                logger.debug(
                    f"booktype [{book_type_str}] bookonly [{CONFIG.get_bool('IMP_AUTOADD_BOOKONLY')}] "
                    f"validtype [{valid_type}]"
                )
                srcname = os.path.join(src_path, name)
                dstname = os.path.join(autoadddir, name)
                try:
                    if savefiles:
                        logger.debug(
                            f"AutoAdd Copying file [{name}] from [{srcname}] to [{dstname}]"
                        )
                        dstname = safe_copy(srcname, dstname)
                    else:
                        logger.debug(
                            f"AutoAdd Moving file [{name}] from [{srcname}] to [{dstname}]"
                        )
                        dstname = safe_move(srcname, dstname)
                    copied = True
                except Exception as why:
                    logger.error(
                        f"AutoAdd - Failed to copy/move file [{name}] {type(why).__name__} [{why!s}] "
                    )
                    return False
                try:
                    os.chmod(syspath(dstname), 0o666)  # make rw for calibre
                except OSError as why:
                    logger.warning(
                        f"Could not set permission of {dstname} because [{why.strerror}]"
                    )
                    # permissions might not be fatal, continue

        if copied and not savefiles:  # do we want to keep the library files?
            logger.debug(f"Removing {src_path}")
            shutil.rmtree(src_path)

    except OSError as why:
        logger.error(f"AutoAdd - Failed because [{why.strerror}]")
        return False

    logger.info(f"Auto Add completed for [{src_path}]")
    return True
