import tarfile
import unittest
from pathlib import Path

from entigram.package_signing import verify_package


PACKAGE_ROOT = Path(__file__).parents[1] / "community-packages"


class TestCommunityPackageArtifacts(unittest.TestCase):
    def test_data_privacy_package_is_signed_and_archive_is_clean(self):
        package = PACKAGE_ROOT / "@entigram" / "data-privacy"
        verification = verify_package(str(package))
        self.assertTrue(verification.ok, verification.errors)

        forbidden = []
        with tarfile.open(package / "package.tar.gz", "r:gz") as archive:
            for member in archive.getmembers():
                parts = Path(member.name).parts
                if any(part == "__MACOSX" or part.startswith("._") for part in parts):
                    forbidden.append(member.name)
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
