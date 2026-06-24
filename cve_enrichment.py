import requests
from datetime import datetime

def compute_score(metadata):
    """
    Compute a score based on the metadata of a CVE.

    Args:
        metadata (dict): A dictionary containing the metadata of a CVE.

    Returns:
        tuple: A tuple containing the computed score (float) and a dictionary of the factors used to compute the score.
    """
    # Initialize the score and factors dictionary
    score = 0.0
    factors = {}

    # Get the registry source
    registry_source = metadata.get('registry_source', '').lower()
    if registry_source == 'nvd':
        score += 0.2
        factors['registry_source'] = 0.2
    elif registry_source == 'mitre':
        score += 0.1
        factors['registry_source'] = 0.1

    # Get the age in days
    published_date = metadata.get('published_date', '')
    if published_date:
        published_date = datetime.strptime(published_date, '%Y-%m-%d')
        age_days = (datetime.now() - published_date).days
        if age_days <= 30:
            score += 0.3
            factors['age_days'] = 0.3
        elif age_days <= 90:
            score += 0.2
            factors['age_days'] = 0.2
        elif age_days <= 180:
            score += 0.1
            factors['age_days'] = 0.1

    # Get the download count
    download_count = metadata.get('download_count', 0)
    if download_count >= 1000:
        score += 0.3
        factors['download_count'] = 0.3
    elif download_count >= 100:
        score += 0.2
        factors['download_count'] = 0.2
    elif download_count >= 10:
        score += 0.1
        factors['download_count'] = 0.1

    return score, factors