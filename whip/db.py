
import logging
import operator

import plyvel
import simplejson as json

from whip.util import dict_diff, ipv4_int_to_bytes


logger = logging.getLogger(__name__)


class Database(object):
    def __init__(self, database_dir, create_if_missing=False):
        logger.debug("Opening database %s", database_dir)
        self.db = plyvel.DB(
            database_dir,
            create_if_missing=create_if_missing,
            write_buffer_size=16 * 1024 * 1024,
            max_open_files=512,
            lru_cache_size=128 * 1024 * 1024)
        self._make_iter()

    def _make_iter(self):
        """Make an iterator for the current database.

        Iterator construction is relatively costly, so reuse it for
        performance reasons. The iterator won't see any data written
        after its construction, but this is not a problem since the data
        set is static.
        """
        self.iter = self.db.iterator(include_key=False)

    def load(self, it):
        """Load data from an importer iterable"""
        extract_datetime = operator.itemgetter('datetime')

        json_encoder = json.JSONEncoder(
            ensure_ascii=True,
            check_circular=False,
            separators=(',', ':'),  # no whitespace

        )
        for n, item in enumerate(it, 1):
            begin_ip_int, end_ip_int, infosets = item

            # The data is a list of dicts with a timestamp. Sort
            # chronologically, putting the most recent information
            # first, and saving only the changed fields for previous
            # versions.
            infosets.sort(key=extract_datetime, reverse=True)
            latest_version = infosets[0]

            previous_versions = [
                dict_diff(d, latest_version)
                for d in infosets[1:]
            ]
            data = (latest_version, previous_versions)
            encoded = json_encoder.encode(data)

            # Store in database. The end IP is stored in the key, the
            # begin IP and the actual information is stored in the
            # value.
            value = ipv4_int_to_bytes(begin_ip_int) + encoded
            self.db.put(
                ipv4_int_to_bytes(end_ip_int),
                value)

            if n % 100000 == 0:
                logger.info('%d records stored', n)

        # Refresh iterator so that it sees the new data
        self._make_iter()

    def lookup(self, ip):
        """Lookup a single IP address in the database

        This either returns the stored information, or `None` if no
        information was found.
        """

        # The database key stores the end IP of all ranges, so a simple
        # seek positions the iterator at the right key (if found).
        self.iter.seek(ip)
        try:
            value = next(self.iter)
        except StopIteration:
            # Past any range in the database: no hit
            return None

        # Check range boundaries. The first 4 bytes store the begin IP.
        # If the IP currently being looked up is in a gap, there is no
        # hit after all.
        if ip < value[:4]:
            return None

        # The remainder of the value is the actual data to return
        return value[4:]
