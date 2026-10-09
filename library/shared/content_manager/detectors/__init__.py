from .content_detector import ContentDetector
from .cs2 import CS2Detector
from .csgo import CSGODetector
from .deadlock import DeadlockDetector
from .dota2 import Dota2Detector
from .gmod import GModDetector
from .goldsrc import GoldSrcDetector
from .hla import HLADetector
from .portal2_ce import Portal2CommunityEditionDetector
from .quake3 import QuakeIDTech3Detector
from .infra import InfraDetector
from .left4dead import Left4DeadDetector
from .portal2 import Portal2Detector
from .portal2_revolution import Portal2RevolutionDetector
from .robot_repair import RobotRepairDetector
from .sbox import SBoxDetector
from .sfm import SFMDetector
from .source1 import Source1Detector
from .source2 import Source2Detector
from .sourcemod import SourceMod
from .swjk2 import StarWarsJediKnights2Detector
from .titanfall1 import TitanfallDetector
from .vindictus import VindictusDetector
from .vampire import VampireDetector
from .workshop import WorkshopDetector
from .bms import BlackMesaDetector
from ..provider import ContentProvider
from ....utils.tiny_path import TinyPath
from .....logger import SourceLogMan

log_manager = SourceLogMan()
logger = log_manager.get_logger('game_detector')


#: Detectors tried against every scanned path, most specific first.
#:
#: Module-level so a detector can delegate to the others -- WorkshopDetector needs
#: to run the real game's detector once it has resolved which game an item belongs
#: to. It is listed first because a workshop path also lives under a Steam library
#: that the generic detectors would otherwise claim.
GAME_DETECTORS: list[ContentDetector] = [
    WorkshopDetector(),
    GoldSrcDetector(),
    SFMDetector(), GModDetector(), InfraDetector(), Left4DeadDetector(), BlackMesaDetector(),
    Portal2Detector(),
    Portal2RevolutionDetector(), Portal2CommunityEditionDetector(), CSGODetector(), SourceMod(), Source1Detector(),
    # VindictusDetector(), TitanfallDetector(),
    SBoxDetector(), CS2Detector(), HLADetector(), Dota2Detector(),
    RobotRepairDetector(), DeadlockDetector(), Source2Detector(),
    StarWarsJediKnights2Detector(), QuakeIDTech3Detector(),
    VampireDetector()
]


def detect_game(path: TinyPath) -> set[ContentProvider]:
    content_providers = set()
    for detector in GAME_DETECTORS:
        results, root_path = detector.scan(path)
        if results:
            logger.info(f"Detected {detector.game()} game: {root_path}")
            content_providers.update(results)
    return content_providers or None
