"""Reviewed successor identities. No API access or runtime authorization."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
# A shallow read-only CI mount need not expose the repository's ancestors.
# Parent traversal saturates at root; importing identities performs no reads.
REPO = HERE.parent.parent.parent
IMAGE = 'ghcr.io/thaynes43/book-copy-writer@sha256:fdc358fcce883a198a710f9415a95e5200b39499a26ab540a9863043a8b2f86c'
APP_IMAGE = 'ghcr.io/thaynes43/haynesnetwork:v0.110.5@sha256:e264e8a63b7a6b8865bfb51ecb38c398534d492ba64c6956960dedd8e6e64c6a'
IMAGE_SOURCE = '7c99b2afbeed3ec04af55493509169e0778d6418'
APP_SOURCE = '8374aeca2456ec7aba035530be4755fd89216cf1'
IMAGE_RECEIPT_SHA256 = '9bc68e6f5c21ac436838786f10ccc93db776108b9e897a7558730e4b9bd99ddc'
LABEL = 'issue825.haynesnetwork/phase'
JOBS = {
    'live': ('frontend', 'issue831-live-byte-baseline-1009-03'),
    'source': ('frontend', 'issue831-copy-source-census-1009-03'),
    'main': ('frontend', 'issue831-copy-selected-1009-03'),
    'll': ('downloads', 'issue831-ll-source-1009-03'),
    'kavita': ('media', 'issue831-kavita-source-1009-03'),
    'lidarr': ('media', 'issue831-lidarr-source-1009-03'),
}
MODULES = {
    'requirements.txt': '830451570988c97907965090ff7d83b327681336527f6f06dc92d0c74411becc',
    'epub_copies.py': 'b79389c89bd5a57b4737ad693d2c209bc513e818343064ec04c8f0fcfb24a7da',
    'epub_metadata.py': 'ce3c5a271cb4c94d91f3154240b4cfbc5e0cc57981969fc5c975a0c0f50eb773',
    'book_copy_writer.py': 'fbfec65f4af933da6ec9aca90536501e4514cebfb8e378584e3085a993493880',
    'bound_census.py': 'd3df953dc7105d9d2535b0d370bb53c266ab46f018beac72652f681d91d52c38',
    'proof_transport.py': '096ba710805623638b87d8c31f6be2653fe57ae0377bf35a27dabf014755e8a5',
}
PREFLIGHT_SHA256 = '3fd41af67460aaacad9fba92b09418c31110fe15bae88dace3e75179ec0557a1'
COLLECTOR_SHA256 = '03f6163873b75f2bf0dc6896851b9da568d5e370ef39b56983eed64def2eb5f0'
LIVE_ENTRY_SHA256 = 'aabc066c3855f3a270c047a075addeaafb0b206abe474fc03134d21b3bf7c3d1'
SCOPE_PROFILE_SHA256 = '3f3cb56027c880e4f21151b8b8d025a217a52df7e9373d13dbac8699d9dcb666'
SELECTED_SCOPE_SHA256 = '1754edf94c3735c5c7cf6a78d30e3bea3b110e7b48a77ef1fea6e16e91c82663'
SELECTED_SCOPE = {
    'Orson Scott Card/Pathfinder/Orson Scott Card - Pathfinder.epub': 'c48cece08187598a5a63a9fc2a330b911cef57393dcb4cd2db0697e9391e89b2',
    'Orson Scott Card/Pathfinder/Pathfinder - Orson Scott Card.epub': 'dca5fba80025aa2f91535d014684018e4e2211e85803cd136d4cda1c95a83f54',
    'Orson Scott Card/Visitors/Visitors - Orson Scott Card.epub': 'c48cece08187598a5a63a9fc2a330b911cef57393dcb4cd2db0697e9391e89b2',
}


def module_source(name):
    packaged = HERE / 'runtime-modules' / name
    if packaged.is_file():
        return packaged
    if name in ('epub_copies.py', 'epub_metadata.py', 'epub_copy_preflight.py'):
        return REPO / 'kubernetes/main/apps/downloads/lazylibrarian/app/epub-convert' / name
    return HERE.parent / name
