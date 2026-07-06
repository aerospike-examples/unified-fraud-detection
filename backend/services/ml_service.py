"""
Rule-Based ML Model Service

This service implements rule-based fraud risk scoring at the ACCOUNT level.
It uses pre-computed features from the account-fact and device-fact KV sets.

Score Calculation (max 115 points, normalized to 0-100):
  - Velocity signals (max 30 pts)
  - Amount signals (max 25 pts)  
  - Counterparty signals (max 25 pts)
  - Device signals (max 15 pts)
  - Lifecycle signals (max 20 pts)

User Risk = max(account_risks)
Flag if User Risk >= 70

Device Flagging:
  - watchlist if: shared_account_count >= 3 AND avg_account_risk >= 70
  - fraud if: flagged_account_count >= 2
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger('fraud_detection.ml')


class MLModelService:
    """
    Rule-based ML Model Service for fraud risk prediction at account level.
    """
    
    def __init__(self):
        self.model_version = "rules-v2.0"
        self.last_prediction_time = None
        
        # Configurable thresholds (calibrated for generated transaction data)
        self.thresholds = {
            # Velocity thresholds (lowered - generated data has high velocity)
            'txn_out_high': 100,           # Was 50, but many accounts have 1000+ txns
            'txn_24h_peak_high': 50,       # Was 20
            'txn_zscore_high': 3.0,        # Was 2.0, z-scores are often huge
            
            # Amount thresholds (lowered - generated txns max at ~$10k)
            'max_out_amt_high': 8000,      # Was 15000
            'avg_out_amt_high': 3000,      # Was 5000
            'amt_zscore_high': 3.0,        # Was 2.0
            
            # Counterparty thresholds (lowered - generated data has ~5 recipients)
            'unique_recipients_high': 10,  # Was 30
            'new_recipient_ratio_high': 0.8, # Was 0.7
            'recipient_entropy_high': 2.0, # Was 3.0
            
            # Device thresholds (lowered)
            'device_count_high': 2,        # Was 3
            'shared_device_count_high': 3, # Was 5
            
            # Lifecycle thresholds
            'new_account_days': 30,
            'new_account_txn_threshold': 10,
            'first_txn_delay_suspicious': 1,
            
            # User flagging threshold (lowered for demo)
            'flag_threshold': 50,          # Was 70
            
            # Device flagging thresholds
            'device_watchlist_shared': 3,
            'device_watchlist_risk': 50,   # Was 70
            'device_fraud_flagged_count': 2,
        }
        
        # Max points per category (total 115, normalized to 100)
        self.max_points = {
            'velocity': 30,
            'amount': 25,
            'counterparty': 25,
            'device': 15,
            'lifecycle': 20,
        }
    
    def predict_account_risk(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict fraud risk score for an ACCOUNT based on its computed features.
        
        Args:
            features: Dictionary containing account-fact features:
                - txn_out_7d, txn_24h_peak, avg_txn_day, max_txn_hr, txn_zscore
                - out_amt_7d, avg_out_amt, max_out_amt, amt_zscore
                - uniq_recip, new_recip_rat, recip_entropy
                - dev_count, shared_dev_ct
                - acct_age_days, first_txn_dly
                
        Returns:
            Dictionary containing:
                - risk_score: Float 0-100
                - risk_factors: List of contributing factors
                - category_scores: Breakdown by category
                - confidence: Model confidence
        """
        self.last_prediction_time = datetime.now()
        
        # Map long feature names (from DB) to short names (used internally)
        # This handles both naming conventions
        name_map = {
            # Velocity features
            'txn_out_count_7d': 'txn_out_7d',
            'txn_out_count_24h_peak': 'txn_24h_peak',
            'avg_txn_per_day_7d': 'avg_txn_day',
            'max_txn_per_hour_7d': 'max_txn_hr',
            'transaction_zscore': 'txn_zscore',
            # Amount features
            'total_out_amount_7d': 'out_amt_7d',
            'avg_out_amount_7d': 'avg_out_amt',
            'max_out_amount_7d': 'max_out_amt',
            'amount_zscore_7d': 'amt_zscore',
            # Counterparty features
            'unique_recipients_7d': 'uniq_recip',
            'new_recipient_ratio_7d': 'new_recip_rat',
            'recipient_entropy_7d': 'recip_entropy',
            # Device features
            'device_count_7d': 'dev_count',
            'shared_device_account_count_7d': 'shared_dev_ct',
            # Lifecycle features
            'account_age_days': 'acct_age_days',
            'first_txn_delay_days': 'first_txn_dly',
        }
        
        # Normalize feature names - accept both long and short names
        normalized = {}
        for key, value in features.items():
            # If it's a long name, map to short name
            if key in name_map:
                normalized[name_map[key]] = value
            else:
                normalized[key] = value
        
        risk_factors = []
        category_scores = {
            'velocity': 0,
            'amount': 0,
            'counterparty': 0,
            'device': 0,
            'lifecycle': 0,
        }
        
        # A. Velocity signals (max 30 pts)
        txn_out = normalized.get('txn_out_7d', 0) or 0
        txn_24h_peak = normalized.get('txn_24h_peak', 0) or 0
        txn_zscore = normalized.get('txn_zscore', 0) or 0
        
        if txn_out > self.thresholds['txn_out_high']:
            category_scores['velocity'] += 15
            risk_factors.append(f"High transaction count ({txn_out} in 7d)")
        
        if txn_24h_peak > self.thresholds['txn_24h_peak_high']:
            category_scores['velocity'] += 10
            risk_factors.append(f"High 24h burst ({txn_24h_peak} transactions)")
        
        if txn_zscore > self.thresholds['txn_zscore_high']:
            category_scores['velocity'] += 5
            risk_factors.append(f"Unusual transaction velocity (z={txn_zscore:.1f})")
        
        # B. Amount signals (max 25 pts)
        max_out_amt = normalized.get('max_out_amt', 0) or 0
        avg_out_amt = normalized.get('avg_out_amt', 0) or 0
        amt_zscore = normalized.get('amt_zscore', 0) or 0
        
        if max_out_amt > self.thresholds['max_out_amt_high']:
            category_scores['amount'] += 10
            risk_factors.append(f"High-value transaction (${max_out_amt:,.2f})")
        
        if avg_out_amt > self.thresholds['avg_out_amt_high']:
            category_scores['amount'] += 10
            risk_factors.append(f"High avg transaction (${avg_out_amt:,.2f})")
        
        if amt_zscore > self.thresholds['amt_zscore_high']:
            category_scores['amount'] += 5
            risk_factors.append(f"Unusual amount pattern (z={amt_zscore:.1f})")
        
        # C. Counterparty signals (max 25 pts)
        uniq_recip = normalized.get('uniq_recip', 0) or 0
        new_recip_rat = normalized.get('new_recip_rat', 0) or 0
        recip_entropy = normalized.get('recip_entropy', 0) or 0
        
        if uniq_recip > self.thresholds['unique_recipients_high']:
            category_scores['counterparty'] += 10
            risk_factors.append(f"Many recipients ({uniq_recip} unique)")
        
        if new_recip_rat > self.thresholds['new_recipient_ratio_high']:
            category_scores['counterparty'] += 10
            risk_factors.append(f"High new recipient ratio ({new_recip_rat:.0%})")
        
        if recip_entropy > self.thresholds['recipient_entropy_high']:
            category_scores['counterparty'] += 5
            risk_factors.append(f"Fan-out pattern (entropy={recip_entropy:.2f})")
        
        # D. Device signals (max 15 pts)
        dev_count = normalized.get('dev_count', 1) or 1
        shared_dev_ct = normalized.get('shared_dev_ct', 0) or 0
        
        if dev_count > self.thresholds['device_count_high']:
            category_scores['device'] += 10
            risk_factors.append(f"Multiple devices ({dev_count} devices)")
        
        if shared_dev_ct > self.thresholds['shared_device_count_high']:
            category_scores['device'] += 5
            risk_factors.append(f"Shared device exposure ({shared_dev_ct} accounts)")
        
        # E. Lifecycle signals (max 20 pts)
        acct_age_days = normalized.get('acct_age_days', 365) or 365
        first_txn_dly = normalized.get('first_txn_dly', 30) or 30
        
        if acct_age_days < self.thresholds['new_account_days'] and txn_out > self.thresholds['new_account_txn_threshold']:
            category_scores['lifecycle'] += 15
            risk_factors.append(f"New account with high activity ({acct_age_days}d old, {txn_out} txns)")
        
        if first_txn_dly < self.thresholds['first_txn_delay_suspicious']:
            category_scores['lifecycle'] += 5
            risk_factors.append(f"Immediate transaction after creation ({first_txn_dly}d delay)")
        
        # Total score = absolute accumulated fraud signal (capped at 100).
        # NOTE: previously normalized against the theoretical all-category max (115),
        # which capped realistic 2-3 category fraud (~55 raw) at ~48 — permanently
        # below the flag threshold of 50, so clear fraud never flagged. Scoring on the
        # raw point total makes the threshold meaningful: 50 = "50+ pts of fraud signal".
        raw_score = sum(category_scores.values())
        risk_score = min(100, raw_score)
        
        # Generate reason string
        if risk_factors:
            reason = " | ".join(risk_factors[:3])
        else:
            reason = "Normal account activity"
        
        # Calculate confidence based on feature completeness
        feature_count = sum(1 for v in normalized.values() if v is not None and v != 0)
        confidence = min(0.95, 0.5 + (feature_count * 0.03))
        
        return {
            "risk_score": round(risk_score, 2),
            "risk_factors": risk_factors,
            "reason": reason,
            "category_scores": category_scores,
            "raw_score": raw_score,
            "confidence": round(confidence, 2),
            "model_version": self.model_version,
            "prediction_time": self.last_prediction_time.isoformat()
        }
    
    def predict_user_risk(self, account_predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Compute user risk as the MAX of all their account risks.
        
        Args:
            account_predictions: List of predict_account_risk results for user's accounts
            
        Returns:
            User risk prediction with highest account's details
        """
        if not account_predictions:
            return {
                "risk_score": 0,
                "risk_factors": [],
                "reason": "No accounts to evaluate",
                "highest_risk_account": None,
                "account_count": 0,
                "model_version": self.model_version,
            }
        
        # Find highest risk account
        highest = max(account_predictions, key=lambda x: x.get('risk_score', 0))
        
        # Aggregate all risk factors
        all_factors = []
        for pred in account_predictions:
            all_factors.extend(pred.get('risk_factors', []))
        
        return {
            "risk_score": highest['risk_score'],
            "risk_factors": list(set(all_factors))[:5],  # Top 5 unique factors
            "reason": highest.get('reason', ''),
            "highest_risk_account": highest,
            "account_count": len(account_predictions),
            "model_version": self.model_version,
            "confidence": highest.get('confidence', 0.5),
        }
    
    def evaluate_device_flagging(self, device_features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate device flagging rules.
        
        Args:
            device_features: Device-fact features
            
        Returns:
            Flagging decision with watchlist and fraud status
        """
        shared_acct_ct = device_features.get('shared_acct_ct', 0) or 0
        avg_acct_risk = device_features.get('avg_acct_risk', 0) or 0
        flag_acct_ct = device_features.get('flag_acct_ct', 0) or 0
        
        watchlist = False
        fraud = False
        reasons = []
        
        # Watchlist rule: shared_account_count >= 3 AND avg_account_risk >= 70
        if (shared_acct_ct >= self.thresholds['device_watchlist_shared'] and 
            avg_acct_risk >= self.thresholds['device_watchlist_risk']):
            watchlist = True
            reasons.append(f"Shared by {shared_acct_ct} accounts with avg risk {avg_acct_risk:.0f}")
        
        # Fraud rule: flagged_account_count >= 2
        if flag_acct_ct >= self.thresholds['device_fraud_flagged_count']:
            fraud = True
            reasons.append(f"Connected to {flag_acct_ct} flagged accounts")
        
        return {
            "watchlist": watchlist,
            "fraud": fraud,
            "reasons": reasons,
            "device_features": device_features,
        }
    
    # Backwards compatibility: old predict_risk method that works at account level
    def predict_risk(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Backwards-compatible method for account risk prediction.
        Maps old feature names to new ones if necessary.
        """
        # Map old feature names to new ones
        mapped_features = {
            'txn_out_7d': features.get('txn_out_7d', features.get('transaction_count', 0)),
            'txn_24h_peak': features.get('txn_24h_peak', 0),
            'avg_txn_day': features.get('avg_txn_day', 0),
            'max_txn_hr': features.get('max_txn_hr', 0),
            'txn_zscore': features.get('txn_zscore', 0),
            'out_amt_7d': features.get('out_amt_7d', features.get('total_amount', 0)),
            'avg_out_amt': features.get('avg_out_amt', features.get('avg_amount', 0)),
            'max_out_amt': features.get('max_out_amt', 0),
            'amt_zscore': features.get('amt_zscore', 0),
            'uniq_recip': features.get('uniq_recip', features.get('unique_recipients', 0)),
            'new_recip_rat': features.get('new_recip_rat', 0),
            'recip_entropy': features.get('recip_entropy', 0),
            'dev_count': features.get('dev_count', features.get('device_count', 1)),
            'shared_dev_ct': features.get('shared_dev_ct', 0),
            'acct_age_days': features.get('acct_age_days', features.get('account_age_days', 365)),
            'first_txn_dly': features.get('first_txn_dly', 0),
        }
        
        return self.predict_account_risk(mapped_features)
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the ML model."""
        return {
            "model_version": self.model_version,
            "model_type": "rule-based",
            "description": "Rule-based fraud scoring at account level. User risk = max(account risks).",
            "thresholds": self.thresholds,
            "max_points": self.max_points,
            "features_used": [
                # Velocity
                "txn_out_7d", "txn_24h_peak", "avg_txn_day", "max_txn_hr", "txn_zscore",
                # Amount
                "out_amt_7d", "avg_out_amt", "max_out_amt", "amt_zscore",
                # Counterparty
                "uniq_recip", "new_recip_rat", "recip_entropy",
                # Device
                "dev_count", "shared_dev_ct",
                # Lifecycle
                "acct_age_days", "first_txn_dly"
            ],
            "last_prediction_time": self.last_prediction_time.isoformat() if self.last_prediction_time else None
        }
    
    def update_thresholds(self, new_thresholds: Dict[str, Any]) -> Dict[str, Any]:
        """Update model thresholds."""
        self.thresholds.update(new_thresholds)
        logger.info(f"Updated ML thresholds: {new_thresholds}")
        return self.thresholds


# Singleton instance
ml_model_service = MLModelService()
