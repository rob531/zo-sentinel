import os
from typing import Dict, List

# Constants
VERDICTS = ['TRUSTED_GENERAL', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED', 'CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'KNOWN_THREAT', 'INSUFFICIENT']
VERDICT_THRESHOLDS: Dict[str, float] = {'TRUSTED_GENERAL': 0.8, 'TRUSTED_RESEARCH': 0.7, 'ENTERPRISE_CONTROLLED': 0.9, 'CAUTION_LIMITED': 0.6, 'HIGH_RISK_ISOLATED': 0.4, 'KNOWN_THREAT': 0.3, 'INSUFFICIENT': 0.2}
VERDICT_EXPIRY_DAYS: Dict[str, int] = {'TRUSTED_GENERAL': 30, 'TRUSTED_RESEARCH': 60, 'ENTERPRISE_CONTROLLED': 90, 'CAUTION_LIMITED': 120, 'HIGH_RISK_ISOLATED': 180, 'KNOWN_THREAT': 240, 'INSUFFICIENT': 360}
RISK_TIERS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
RISK_THRESHOLDS: Dict[str, float] = {'CRITICAL': 0.9, 'HIGH': 0.7, 'MEDIUM': 0.5, 'LOW': 0.3}

# Verdict Taxonomy Constants
class VerdictTaxonomy:
    def __init__(self):
        self.VERDICTS = VERDICTS
        self.VERDICT_THRESHOLDS = VERDICT_THRESHOLDS
        self.VERDICT_EXPIRY_DAYS = VERDICT_EXPIRY_DAYS
        self.RISK_TIERS = RISK_TIERS
        self.RISK_THRESHOLDS = RISK_THRESHOLDS

    def get_verdicts(self) -> List[str]:
        return self.VERDICTS

    def get_verdict_thresholds(self) -> Dict[str, float]:
        return self.VERDICT_THRESHOLDS

    def get_verdict_expiry_days(self) -> Dict[str, int]:
        return self.VERDICT_EXPIRY_DAYS

    def get_risk_tiers(self) -> List[str]:
        return self.RISK_TIERS

    def get_risk_thresholds(self) -> Dict[str, float]:
        return self.RISK_THRESHOLDS

# Initialize Verdict Taxonomy
verdict_taxonomy = VerdictTaxonomy()