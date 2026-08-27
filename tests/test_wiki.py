import unittest

from badupdateprep.wiki import PAGES, load_page, wiki_dir


class WikiLocal(unittest.TestCase):
    def test_all_pages_exist(self):
        root = wiki_dir()
        self.assertTrue(root.is_dir())
        for filename, title in PAGES:
            text = load_page(filename)
            self.assertTrue(text.strip(), msg=title)
            self.assertNotIn("Missing wiki page", text)

    def test_how_to_use_has_live_warning(self):
        text = load_page("how_to_use.md").lower()
        self.assertIn("xbox live", text)
        self.assertIn("17559", text)
