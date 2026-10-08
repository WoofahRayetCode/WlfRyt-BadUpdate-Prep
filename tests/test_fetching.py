import hashlib
import io
import json
import os
import time
import unittest
import urllib.error
from dataclasses import replace
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory

from badupdateprep import catalog
from badupdateprep.cache import DownloadCache
from badupdateprep.catalog import Channel, Pin
from badupdateprep.downloader import DownloadError, RetryPolicy
from badupdateprep.github import (
    RateLimited,
    Resolved,
    list_releases,
    parse_releases,
    pick_asset,
    pick_release,
    pin_url,
    resolve,
    resolve_pinned,
)
from badupdateprep.pipeline import Env, ItemUpdate, ensure_artifact
from tests.httpd import FaultyServer

DATA = os.urandom(300_000)
SHA = hashlib.sha256(DATA).hexdigest()


def release(tag, assets, prerelease=False, draft=False):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "assets": [
            {"name": n, "browser_download_url": f"https://x/{tag}/{n}", "size": 10, **({"digest": d} if d else {})}
            for n, d in assets
        ],
    }


class FakeResp:
    def __init__(self, body: bytes, headers=None):
        self._b = io.BytesIO(body)
        self.headers = headers or {}
        self.status = 200

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def close(self):
        pass


def http_error(code, headers=None):
    hdrs = Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return urllib.error.HTTPError("http://x", code, "err", hdrs, io.BytesIO(b""))


class Metadata(unittest.TestCase):
    def test_parse_skips_drafts_and_reads_digest(self):
        rels = parse_releases(
            [
                release("v2", [("a.zip", "sha256:" + "a" * 64)], draft=True),
                release("v1", [("a.zip", "sha256:" + "b" * 64), ("src.tar", None)]),
            ]
        )
        self.assertEqual([r.tag for r in rels], ["v1"])
        self.assertEqual(rels[0].assets[0].sha256, "b" * 64)
        self.assertIsNone(rels[0].assets[1].sha256)

    def test_prerelease_only_repo(self):
        rels = parse_releases([release("vPB1.0", [("ABad.zip", None)], prerelease=True)])
        with self.assertRaises(DownloadError):
            pick_release(rels, allow_prerelease=False)
        self.assertEqual(pick_release(rels, allow_prerelease=True).tag, "vPB1.0")

    def test_pick_asset_never_falls_back_to_first_zip(self):
        rel = parse_releases([release("v1", [("source.zip", None), ("tools.zip", None)])])[0]
        with self.assertRaises(DownloadError) as cm:
            pick_asset(rel, r"(?i)usb.*\.zip$")
        self.assertIn("source.zip", str(cm.exception))
        self.assertEqual(cm.exception.kind, "layout")

    def test_pick_asset_ambiguous_is_an_error(self):
        rel = parse_releases([release("v1", [("a-usb.zip", None), ("b-usb.zip", None)])])[0]
        with self.assertRaises(DownloadError) as cm:
            pick_asset(rel, r"usb.*\.zip$")
        self.assertIn("several", str(cm.exception))

    def test_pick_asset_unique(self):
        rel = parse_releases([release("v1.3", [("Xbox360BadUpdate-Retail-USB-v1.3.zip", None), ("Tools.zip", None)])])[0]
        self.assertEqual(pick_asset(rel, catalog.BADUPDATE.asset_re).name, "Xbox360BadUpdate-Retail-USB-v1.3.zip")

    def test_rate_limit_message_is_actionable(self):
        reset = str(int(time.time()) + 600)

        def opener(req, timeout):
            raise http_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})

        with self.assertRaises(RateLimited) as cm:
            list_releases("o", "r", opener=opener)
        msg = str(cm.exception)
        self.assertIn("Tested versions", msg)
        self.assertIn("min", msg)
        self.assertEqual(cm.exception.kind, "rate_limit")

    def test_etag_is_sent_and_304_uses_cache(self):
        body = json.dumps([release("v1", [("a.zip", None)])]).encode()
        seen = []

        def opener(req, timeout):
            seen.append(req.get_header("If-none-match"))
            if seen[-1]:
                raise http_error(304)
            return FakeResp(body, {"ETag": '"abc"'})

        with TemporaryDirectory() as td:
            a = list_releases("o", "r", cache_dir=Path(td), opener=opener)
            b = list_releases("o", "r", cache_dir=Path(td), opener=opener)
        self.assertEqual(seen, [None, '"abc"'])
        self.assertEqual(a[0].tag, b[0].tag)

    def test_token_only_goes_to_api(self):
        seen = {}

        def opener(req, timeout):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return FakeResp(b"[]")

        list_releases("o", "r", token="T", opener=opener)
        self.assertEqual(seen["authorization"], "Bearer T")

    def test_pinned_channel_makes_no_api_call(self):
        def boom(req, timeout):
            raise AssertionError("pinned resolution must not hit the network")

        for art in catalog.CATALOG.values():
            if art.source == "manual":
                continue
            r = resolve(art, Channel.PINNED, opener=boom)
            self.assertEqual(r.provenance, "pinned")
            self.assertEqual(r.hashes[0][0], "sha256")
            self.assertEqual(len(r.hashes[0][1]), 64)

    def test_pin_urls(self):
        self.assertEqual(
            pin_url(catalog.BADUPDATE),
            "https://github.com/grimdoomer/Xbox360BadUpdate/releases/download/BadUpdate-v1.3/Xbox360BadUpdate-Retail-USB-v1.3.zip",
        )
        self.assertEqual(pin_url(catalog.RBB_TRIAL), catalog.RBB_TRIAL_URL)

    def test_trial_always_uses_its_pin_even_on_latest(self):
        r = resolve(catalog.RBB_TRIAL, Channel.LATEST, opener=lambda *a: (_ for _ in ()).throw(AssertionError("no net")))
        self.assertEqual(r.size, 286_678_579)
        self.assertEqual(dict(r.hashes)["md5"], "9511d5a87caeca0f70183c06b22bcd3e")

    def test_latest_channel_picks_digest(self):
        body = json.dumps([release("v9", [("Xbox360BadUpdate-Retail-USB-v9.zip", "sha256:" + "c" * 64)])]).encode()
        r = resolve(catalog.BADUPDATE, Channel.LATEST, opener=lambda req, t: FakeResp(body))
        self.assertEqual((r.tag, r.provenance), ("v9", "github-digest"))
        self.assertEqual(r.hashes, (("sha256", "c" * 64),))


class Cache(unittest.TestCase):
    def _res(self, td, size=len(DATA), hashes=(("sha256", SHA),), key="badupdate"):
        art = replace(catalog.BADUPDATE, key=key)
        return Resolved(art, "v1", "p.zip", "http://x/p.zip", size, hashes, "pinned")

    def test_truncated_file_is_not_cached_regression(self):
        # Old behaviour: any file > 1 KB counted as cached, so a cut-off download was reused forever.
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            r = self._res(td)
            p = cache.path_for_resolved(r)
            p.parent.mkdir(parents=True)
            p.write_bytes(DATA[:200_000])
            self.assertEqual(cache.lookup(r), "corrupt")

    def test_same_size_wrong_content_is_corrupt(self):
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            r = self._res(td)
            p = cache.path_for_resolved(r)
            p.parent.mkdir(parents=True)
            p.write_bytes(bytes(len(DATA)))
            self.assertEqual(cache.lookup(r), "corrupt")

    def test_hit_after_record_and_fast_path(self):
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            r = self._res(td)
            self.assertEqual(cache.lookup(r), "miss")
            p = cache.path_for_resolved(r)
            p.parent.mkdir(parents=True)
            p.write_bytes(DATA)
            self.assertEqual(cache.lookup(r), "hit")
            cache.record(r, p, SHA)
            self.assertEqual(cache.lookup(r), "hit")
            # touching the file invalidates the fast path and forces a re-hash
            p.write_bytes(DATA[:-1] + b"\x00")
            self.assertEqual(cache.lookup(r), "corrupt")

    def test_unverified_zip_must_be_a_real_zip(self):
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            r = Resolved(replace(catalog.BADUPDATE), "v1", "p.zip", "u", 100, (), "unverified")
            p = cache.path_for_resolved(r)
            p.parent.mkdir(parents=True)
            p.write_bytes(b"x" * 100)
            self.assertEqual(cache.lookup(r), "corrupt")

    def test_adopt_legacy_layout(self):
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            r = self._res(td)
            legacy = Path(td) / r.art.key / r.asset
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(DATA)
            self.assertTrue(cache.adopt_legacy(r))
            self.assertEqual(cache.lookup(r), "hit")
            self.assertFalse(legacy.exists())

    def test_adopt_legacy_rejects_bad_file(self):
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            r = self._res(td)
            legacy = Path(td) / r.art.key / r.asset
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(DATA[:5000])
            self.assertFalse(cache.adopt_legacy(r))

    def test_latest_cached_and_prune(self):
        with TemporaryDirectory() as td:
            cache = DownloadCache(Path(td))
            paths_ = []
            for tag in ("v1", "v2", "v3"):
                r = replace(self._res(td), tag=tag)
                p = cache.path_for_resolved(r)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(DATA)
                cache.record(r, p, SHA)
                paths_.append(p)
            self.assertEqual(cache.latest_cached("badupdate").tag, "v3")
            freed = cache.prune(keep=1)
            self.assertEqual(freed, 2 * len(DATA))
            self.assertFalse(paths_[0].exists())
            self.assertTrue(paths_[2].exists())


class Ensure(unittest.TestCase):
    def _art(self, srv, data=DATA):
        pin = Pin("v1", "x.zip", len(data), hashlib.sha256(data).hexdigest(), url=srv.url("/x.zip"))
        return replace(catalog.BADUPDATE, key="badupdate", pin=pin)

    def _env(self, td, **kw):
        return Env(app_dir=Path(td), sleep=lambda s: None, retry=RetryPolicy(attempts=3, base=0), **kw)

    def test_download_then_cached_without_network(self):
        with FaultyServer({"/x.zip": DATA}) as srv, TemporaryDirectory() as td:
            art, env, events = self._art(srv), self._env(td), []
            got = ensure_artifact(art, env, events.append)
            self.assertFalse(got.cached)
            self.assertEqual(got.path.read_bytes(), DATA)
            n = len(srv.requests)
            again = ensure_artifact(art, env, events.append)
            self.assertTrue(again.cached)
            self.assertEqual(len(srv.requests), n, "a verified cache hit must not touch the network")
            statuses = [e.status for e in events if isinstance(e, ItemUpdate)]
            self.assertIn("downloading", statuses)
            self.assertIn("cached", statuses)

    def test_truncated_cache_entry_is_redownloaded(self):
        with FaultyServer({"/x.zip": DATA}) as srv, TemporaryDirectory() as td:
            art, env = self._art(srv), self._env(td)
            p = env.cache.path_for(art.key, "v1", "x.zip")
            p.parent.mkdir(parents=True)
            p.write_bytes(DATA[:100_000])
            got = ensure_artifact(art, env, lambda e: None)
            self.assertEqual(got.path.read_bytes(), DATA)

    def test_offline_falls_back_to_cached_version(self):
        with FaultyServer({"/x.zip": DATA}) as srv, TemporaryDirectory() as td:
            art = self._art(srv)
            env = self._env(td)
            ensure_artifact(art, env, lambda e: None)

            def dead(req, timeout):
                raise urllib.error.URLError("no route to host")

            # latest channel, GitHub unreachable -> use what is already verified on disk
            offline = self._env(td, channel=Channel.LATEST, opener=dead)
            logs = []
            got = ensure_artifact(art, offline, logs.append)
            self.assertTrue(got.cached and got.offline)
            self.assertTrue(any("cached" in getattr(e, "text", "") for e in logs))

    def test_offline_with_empty_cache_raises(self):
        with TemporaryDirectory() as td:
            def dead(req, timeout):
                raise urllib.error.URLError("no route to host")

            env = self._env(td, channel=Channel.LATEST, opener=dead)
            with self.assertRaises(DownloadError):
                ensure_artifact(catalog.BADUPDATE, env, lambda e: None)

    def test_legacy_cache_is_adopted_not_redownloaded(self):
        with FaultyServer({"/x.zip": DATA}) as srv, TemporaryDirectory() as td:
            art = self._art(srv)
            legacy = Path(td) / "downloads" / art.key / "x.zip"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(DATA)
            got = ensure_artifact(art, self._env(td), lambda e: None)
            self.assertTrue(got.cached)
            self.assertEqual(srv.requests, [])


if __name__ == "__main__":
    unittest.main()
