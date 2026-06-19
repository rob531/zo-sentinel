import math
from datetime import datetime, timedelta
import pytz # For handling timezone-aware datetimes consistently

# --- Configuration Constants ---
# These constants define the weighting and behavior of each scoring component.
# Adjusting these values will change the sensitivity and overall score distribution.

# Max points for each component. Sum should ideally be 100 for a 0-100 scale.
MAX_AGE_SCORE = 40.0
MAX_LAST_UPDATED_SCORE = 20.0
MAX_UPDATE_FREQ_SCORE = 20.0
MAX_VERSION_CHURN_SCORE = 10.0
MAX_MULTI_REGISTRY_SCORE = 10.0

# Age scoring parameters (exponential growth towards max score)
# A higher value means it takes longer for a package to reach its full age score potential.
AGE_HALF_LIFE_DAYS = 180  # Package reaches ~50% of max age score at 180 days, ~90% at ~600 days.

# Last updated scoring parameters (exponential decay from max score)
# A higher value means the score decays slower for older last_updated dates.
LAST_UPDATED_HALF_LIFE_DAYS = 60 # Score drops by half after 60 days of no updates.

# Update frequency scoring parameters (Gaussian-like, centered around ideal)
# IDEAL_UPDATE_FREQ_DAYS: The update frequency (in days) considered optimal.