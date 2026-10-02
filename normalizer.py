import re
import unicodedata

# Common noise tags to strip from titles
NOISE_PATTERNS = [
    r'\[\s*(web|cd|vinyl|flac|mp3|lossless|remastered|bonus\s*track|explicit|clean)\s*\]',
    r'\(\s*(web|cd|vinyl|flac|mp3|lossless|remastered|bonus\s*track|explicit|clean)\s*\)',
    r'\[\s*\d{3,4}\s*kbps\s*\]',
    r'\(\s*\d{3,4}\s*kbps\s*\)',
    r'\[\s*[A-Z0-9_-]{4,15}\s*\]',  # Catalog numbers like [ASOT123], [CLHR138]
    r'\(official\s*(audio|video|music\s*video)\)',
    r'\[official\s*(audio|video|music\s*video)\]',
    r'\(lyrics?\s*(video)?\)',
    r'\[lyrics?\s*(video)?\]',
    r'\(out\s*now\)',
    r'\[out\s*now\]',
]

# Patterns that indicate a mix, remix, or edit
MIX_KEYWORDS = {
    'mix', 'remix', 'edit', 'dub', 'club', 'extended', 'original', 'radio',
    'version', 'instrumental', 'acoustic', 'bootleg', 're-edit', 'rework',
    'cut', 'intro', 'outro', 'vip', 'mashup'
}

MAIN_MIX_TERMS = {
    'original mix', 'extended mix', 'club mix', 'extended club mix', 
    'original club mix', 'original', 'extended', 'club', ''
}

EDIT_MIX_TERMS = {
    'radio edit', 'original edit', 'short edit', 'video edit', 'radio mix', 
    'single edit', 'edit', 'radio cut', 'single mix'
}


def clean_unicode(text: str) -> str:
    """Normalize unicode characters to ASCII where possible."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', text)
    return text.encode('ascii', 'ignore').decode('ascii')


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    if not text:
        return ""
    text = clean_unicode(text).lower()
    # Replace & with 'and'
    text = re.sub(r'\s*&\s*', ' and ', text)
    # Remove non-alphanumeric except space
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_artist(artist_raw: str):
    """
    Extract primary artist and all collaborators.
    Examples:
        'Above & Beyond feat. Richard Bedford' -> ('above and beyond', ['above and beyond', 'richard bedford'])
        'Paul van Dyk vs. Starkillers' -> ('paul van dyk', ['paul van dyk', 'starkillers'])
        'Aly & Fila pres. A&Z' -> ('aly and fila', ['aly and fila', 'a and z'])
    """
    if not artist_raw:
        return "", []

    cleaned = clean_unicode(artist_raw)
    
    # Split by explicit collaboration tokens: feat, ft, featuring, vs, pres, presents, with
    pattern = r'\s+(?:feat\.?|ft\.?|featuring|vs\.?|pres\.?|presents|with)\s+'
    parts = re.split(pattern, cleaned, flags=re.IGNORECASE)
    parts = [normalize_text(p) for p in parts if p.strip()]

    primary = parts[0] if parts else normalize_text(artist_raw)
    
    # Also extract individual names if separated by ' & ' or ' / ' or ', '
    sub_artists = []
    for p in parts:
        sub_artists.append(p)
        subs = re.split(r'\s+(?:and|&|\/|\,)\s+', p)
        for s in subs:
            s_norm = normalize_text(s)
            if s_norm and s_norm not in sub_artists:
                sub_artists.append(s_norm)

    return primary, sub_artists


def extract_title_and_mix(title_raw: str):
    """
    Separates the base track title from mix/remix information.
    Examples:
        'Sun & Moon (Club Mix)' -> ('sun and moon', 'Club Mix', 'main')
        'Sun & Moon (Radio Edit)' -> ('sun and moon', 'Radio Edit', 'edit')
        'Sun & Moon (Above & Beyond Acoustic Mix)' -> ('sun and moon', 'Above & Beyond Acoustic Mix', 'acoustic')
        'Exploration of Space [Cosmic Gate Remix]' -> ('exploration of space', 'Cosmic Gate Remix', 'remix')
        'Southern Sun' -> ('southern sun', 'Original Mix', 'main')
    """
    if not title_raw:
        return "", "Original Mix", "main"

    title = title_raw
    # Strip known noise tags
    for pat in NOISE_PATTERNS:
        title = re.sub(pat, '', title, flags=re.IGNORECASE).strip()

    # Find bracketed expressions: (...) or [...]
    mix_candidate = ""
    bracket_matches = list(re.finditer(r'[\(\[]([^\)\]]+)[\)\]]', title))
    
    clean_title = title
    if bracket_matches:
        # Check from right to left
        for match in reversed(bracket_matches):
            content = match.group(1).strip()
            content_lower = content.lower()
            words = set(re.findall(r'\b\w+\b', content_lower))
            if words.intersection(MIX_KEYWORDS):
                mix_candidate = content
                # Remove this bracket from the base title
                clean_title = title[:match.start()] + title[match.end():]
                clean_title = clean_title.strip()
                break

    # If no bracketed mix found, check for ' - ... Mix' at the end
    if not mix_candidate:
        dash_match = re.search(r'\s+-\s+([^\-]+(?:mix|remix|edit|dub|rework|bootleg))\s*$', title, re.IGNORECASE)
        if dash_match:
            mix_candidate = dash_match.group(1).strip()
            clean_title = title[:dash_match.start()].strip()

    norm_base_title = normalize_text(clean_title)
    
    # Classify mix
    if not mix_candidate:
        mix_candidate = "Original Mix"
        mix_type = "main"
    else:
        mix_type = classify_mix(mix_candidate)

    return norm_base_title, mix_candidate, mix_type


def classify_mix(mix_name: str) -> str:
    """
    Classifies a mix into:
      'main'         : Original / Extended / Club Mix
      'edit'         : Radio Edit / Short Cut
      'dub'          : Dub Mix
      'instrumental' : Instrumental
      'acoustic'     : Acoustic
      'remix'        : Specific artist remix (e.g. Cosmic Gate Remix)
    """
    norm = normalize_text(mix_name)
    if norm in MAIN_MIX_TERMS:
        return "main"
    if norm in EDIT_MIX_TERMS:
        return "edit"
    if 'dub' in norm:
        return "dub"
    if 'instrumental' in norm:
        return "instrumental"
    if 'acoustic' in norm:
        return "acoustic"
    if 'remix' in norm or 'rework' in norm or 'bootleg' in norm:
        return "remix"
    return "main"


def are_mixes_compatible(mix_type1: str, mix_name1: str, mix_type2: str, mix_name2: str):
    """
    Determines if two mixes represent the same song version, a cut/edit, or a different remix.
    Returns:
      'SAME_VERSION'   : e.g. Original Mix vs Extended Mix (both 'main')
      'EDIT_VS_FULL'   : e.g. Radio Edit ('edit') vs Club Mix ('main')
      'SAME_REMIX'     : e.g. both are 'Cosmic Gate Remix'
      'DIFFERENT_REMIX': e.g. 'Dub Mix' vs 'Club Mix' or 'Remix A' vs 'Remix B'
    """
    norm1 = normalize_text(mix_name1)
    norm2 = normalize_text(mix_name2)

    # Identical mix name
    if norm1 == norm2:
        return "SAME_VERSION"

    # Both are standard main mixes (Original, Extended, Club)
    if mix_type1 == "main" and mix_type2 == "main":
        return "SAME_VERSION"

    # One is Radio Edit, one is Main (Club/Extended)
    if (mix_type1 == "main" and mix_type2 == "edit") or (mix_type1 == "edit" and mix_type2 == "main"):
        return "EDIT_VS_FULL"

    # Both are edits
    if mix_type1 == "edit" and mix_type2 == "edit":
        return "SAME_VERSION"

    # Compare remix names
    if mix_type1 == "remix" and mix_type2 == "remix":
        from rapidfuzz import fuzz
        if fuzz.token_sort_ratio(norm1, norm2) >= 80:
            return "SAME_REMIX"
        return "DIFFERENT_REMIX"

    # Otherwise different arrangements (dub vs vocal, acoustic vs club, etc.)
    return "DIFFERENT_REMIX"
