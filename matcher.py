from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from rapidfuzz import fuzz

from config import (
    DURATION_DELTA_UPGRADE,
    DURATION_DELTA_SAME,
    BITRATE_DELTA_UPGRADE,
    FUZZY_MATCH_THRESHOLD
)
from normalizer import are_mixes_compatible
from indexer import LibraryIndexer


@dataclass
class CandidateMatch:
    candidate: Dict[str, Any]      # Library track metadata
    status: str                    # UPGRADE_DURATION, UPGRADE_QUALITY, INFERIOR_DURATION, etc.
    status_label: str              # User-facing short badge
    delete_target: Optional[str]   # 'library', 'incoming', or None
    target_path: Optional[str]     # Exact path of file to delete if confirmed
    recommended_check: bool        # True if pre-checked by default
    reason: str                    # Explanation
    match_score: int               # Fuzzy score (0-100)


@dataclass
class TrackCluster:
    incoming: Dict[str, Any]
    matches: List[CandidateMatch]


def format_duration(seconds: float) -> str:
    """Formats seconds into mm:ss."""
    s = int(seconds)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


class TrackMatcher:
    def __init__(self, indexer: LibraryIndexer):
        self.indexer = indexer

    def match_track(self, incoming_meta: Dict[str, Any]) -> Optional[TrackCluster]:
        """
        Finds matching library tracks for an incoming file and generates recommendations.
        """
        raw_candidates = self.indexer.find_candidates(
            incoming_meta['title_norm'], 
            incoming_meta.get('artist_norm', '')
        )

        matches = []
        incoming_path = incoming_meta['path']

        for cand in raw_candidates:
            cand_dict = dict(cand)
            cand_path = cand_dict['path']

            # Skip comparing against itself if it's already in the library index
            if incoming_path and cand_path.lower() == incoming_path.lower():
                continue

            # Fuzzy verification
            title_score = fuzz.token_set_ratio(incoming_meta['title_norm'], cand_dict['title_norm'])
            artist_score = fuzz.token_set_ratio(incoming_meta['artist_norm'], cand_dict['artist_norm'])
            
            # Weighted match score
            if incoming_meta['artist_norm'] and cand_dict['artist_norm']:
                match_score = int(0.6 * title_score + 0.4 * artist_score)
            else:
                match_score = title_score

            if match_score < FUZZY_MATCH_THRESHOLD:
                continue

            # Compare Mixes
            mix_compat = are_mixes_compatible(
                incoming_meta.get('mix_type', 'main'),
                incoming_meta.get('mix_name', 'Original Mix'),
                cand_dict.get('mix_type', 'main'),
                cand_dict.get('mix_name', 'Original Mix')
            )

            dur_diff = incoming_meta['duration'] - cand_dict['duration']
            cand_dur_str = format_duration(cand_dict['duration'])
            inc_dur_str = format_duration(incoming_meta['duration'])

            # Case 1: Different Remix
            if mix_compat == "DIFFERENT_REMIX":
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="DIFFERENT_REMIX",
                    status_label="DIFFERENT REMIX",
                    delete_target=None,
                    target_path=None,
                    recommended_check=False,
                    reason=f"Different Remix ({incoming_meta['mix_name']} vs {cand_dict['mix_name']})",
                    match_score=match_score
                ))
                continue

            # Case 2: Duration Difference
            # 2A: Incoming is substantially longer (Club/Extended Mix acquired)
            if dur_diff > DURATION_DELTA_UPGRADE:
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="UPGRADE_DURATION",
                    status_label="UPGRADE (DELETE OLD)",
                    delete_target="library",
                    target_path=cand_path,
                    recommended_check=True,
                    reason=f"Duration Upgrade (+{int(dur_diff)}s longer Extended/Full mix)",
                    match_score=match_score
                ))
                continue

            # 2B: Incoming is substantially shorter (Radio cut / Short edit)
            if dur_diff < -DURATION_DELTA_UPGRADE:
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="INFERIOR_DURATION",
                    status_label="INFERIOR (DELETE NEW)",
                    delete_target="incoming",
                    target_path=incoming_path,
                    recommended_check=True,
                    reason=f"Inferior Duration (-{int(abs(dur_diff))}s shorter; library has full {cand_dur_str})",
                    match_score=match_score
                ))
                continue

            # Case 3: Same version (Duration within threshold) -> Compare Quality / Bitrate
            inc_lossless = incoming_meta.get('is_lossless', 0)
            cand_lossless = cand_dict.get('is_lossless', 0)
            inc_bitrate = incoming_meta.get('bitrate', 0)
            cand_bitrate = cand_dict.get('bitrate', 0)

            # Quality: Lossless vs Lossy
            if inc_lossless and not cand_lossless:
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="UPGRADE_QUALITY",
                    status_label="UPGRADE (DELETE OLD)",
                    delete_target="library",
                    target_path=cand_path,
                    recommended_check=True,
                    reason=f"Quality Upgrade (Lossless {incoming_meta['format']} vs {cand_dict['format']} {cand_bitrate}k)",
                    match_score=match_score
                ))
                continue

            if not inc_lossless and cand_lossless:
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="INFERIOR_QUALITY",
                    status_label="INFERIOR (DELETE NEW)",
                    delete_target="incoming",
                    target_path=incoming_path,
                    recommended_check=True,
                    reason=f"Inferior Quality ({incoming_meta['format']} {inc_bitrate}k vs Library Lossless {cand_dict['format']})",
                    match_score=match_score
                ))
                continue

            # Quality: Bitrate differences
            if inc_bitrate - cand_bitrate >= BITRATE_DELTA_UPGRADE:
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="UPGRADE_QUALITY",
                    status_label="UPGRADE (DELETE OLD)",
                    delete_target="library",
                    target_path=cand_path,
                    recommended_check=True,
                    reason=f"Bitrate Upgrade ({inc_bitrate}k vs {cand_bitrate}k)",
                    match_score=match_score
                ))
                continue

            if cand_bitrate - inc_bitrate >= BITRATE_DELTA_UPGRADE:
                matches.append(CandidateMatch(
                    candidate=cand_dict,
                    status="INFERIOR_QUALITY",
                    status_label="INFERIOR (DELETE NEW)",
                    delete_target="incoming",
                    target_path=incoming_path,
                    recommended_check=True,
                    reason=f"Inferior Bitrate ({inc_bitrate}k vs Library {cand_bitrate}k)",
                    match_score=match_score
                ))
                continue

            # Exact or Equal Duplicate
            matches.append(CandidateMatch(
                candidate=cand_dict,
                status="EXACT_DUPLICATE",
                status_label="DUPLICATE (DELETE NEW)",
                delete_target="incoming",
                target_path=incoming_path,
                recommended_check=True,
                reason=f"Duplicate (Same duration {inc_dur_str} ≈ {cand_dur_str}, similar bitrate)",
                match_score=match_score
            ))

        if not matches:
            return None

        return TrackCluster(incoming=incoming_meta, matches=matches)
