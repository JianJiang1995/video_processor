"""Keep spatial scene, image quality, and recovery transitions independent."""
from copy import deepcopy
import math


def normalize_quality_flags(visibility):
    result = dict(visibility or {})
    status = result.get('status')
    if status == 'foggy':
        result['fog'] = True
    elif status in {'blurred', 'blocked', 'uncertain', 'out_of_body'}:
        result['fog'] = False
    if status == 'out_of_body':
        result['out_of_body'] = True
    if result.get('fog') or status != 'clear' or result.get('out_of_body'):
        result['fog_cleared'] = False
    return result


def fog_quality_disagreement(visual, review):
    """A rejected spatial label must not discard contradictory quality evidence."""
    visibility = (visual or {}).get('visibility') or {}
    if not review.get('success') or not (visibility.get('fog') or visibility.get('status') == 'foggy'):
        return False
    if review.get('classification') in {'external_body', 'trocar_transition'}:
        return False
    if review.get('image_quality') not in {'clear', 'blurred', 'blocked'}:
        return False
    confidence = review.get('confidence')
    if review.get('rejected_reason') in {'bag_requires_corroborating_visual_observations',
                                       'external_scene_requires_distinct_frame_evidence'}:
        confidence = review.get('proposed_confidence', confidence)
    try:
        confidence = float(confidence or 0)
    except (TypeError, ValueError):
        return False
    return math.isfinite(confidence) and .75 <= confidence <= 1


def apply_visibility_review(visual, review, min_confidence=.75):
    result = deepcopy(visual or {})
    if unresolved_scene_transition(review):
        quality = review.get('image_quality')
        result['visibility'] = {'status': quality if quality in {'blurred', 'blocked'} else 'uncertain',
                                'fog': False, 'fog_cleared': False,
                                'out_of_body': False, 'confidence': 0,
                                'evidence': review.get('evidence', ''),
                                'evidence_source': 'unconfirmed_scene_transition'}
        return result
    if fog_quality_disagreement(result, review) and (
            review.get('classification') in {'uncertain', 'foggy_inside'}
            or review.get('rejected_reason')):
        result.setdefault('visibility_before_quality_dispute', dict(result.get('visibility') or {}))
        result['visibility'] = {'status': 'uncertain', 'fog': False, 'fog_cleared': False,
                                'out_of_body': False, 'confidence': 0,
                                'evidence': review.get('evidence', ''),
                                'evidence_source': 'disputed_image_quality'}
        return result
    try:
        confidence = float(review.get('confidence') or 0)
    except (TypeError, ValueError):
        confidence = 0
    if not review.get('success') or not math.isfinite(confidence) or not max(.75, min_confidence) <= confidence <= 1:
        return result
    scene = review.get('classification')
    visibility = dict(result.get('visibility') or {})
    primary_scene = (result.get('scene') or {}).get('classification')
    if primary_scene in {'external_body', 'trocar_transition'} and scene in {
            'intra_abdominal', 'foggy_inside', 'specimen_bag_inside'}:
        visibility.update(status='uncertain', fog=False, fog_cleared=False, out_of_body=False,
                          confidence=0, evidence_source='disputed_scene')
        result['visibility'] = visibility
        return result
    secondary_fog = scene == 'foggy_inside' or review.get('image_quality') == 'foggy'
    if scene not in {'external_body', 'trocar_transition'} and secondary_fog and (
            (visibility.get('status') == 'clear' and not visibility.get('fog')
             and float(visibility.get('confidence') or 0) >= .75)
            or visibility.get('evidence_source') == 'disputed_image_quality'):
        visibility.update(status='uncertain', fog=False, fog_cleared=False, confidence=0,
                          evidence_source='disputed_image_quality')
        result['visibility'] = visibility
        return result
    updates = {'confidence': review['confidence'], 'evidence': review.get('evidence', ''),
               'evidence_source': 'visibility_secondary_review'}
    if scene in {'external_body', 'trocar_transition'}:
        updates.update(status='out_of_body', out_of_body=True, fog=False, fog_cleared=False)
    elif scene == 'foggy_inside':
        updates.update(status='foggy', out_of_body=False, fog=True, fog_cleared=False)
    elif scene in {'intra_abdominal', 'specimen_bag_inside'}:
        updates['out_of_body'] = False
        quality = review.get('image_quality')
        # Being inside the abdomen says nothing about whether fog has cleared.
        if quality in {'clear', 'foggy', 'blurred', 'blocked', 'uncertain'}:
            updates.update(status=quality, fog=quality == 'foggy', fog_cleared=False)
        else:
            updates.pop('confidence')
    visibility.update(updates)
    if updates.get('status'):
        # Secondary samples can differ from the primary samples; do not mix indices.
        visibility.pop('fog_frame_indices', None)
        visibility.pop('clear_frame_indices', None)
    if visibility.get('out_of_body') or visibility.get('fog') or visibility.get('status') != 'clear':
        visibility['fog_cleared'] = False
    result['visibility'] = visibility
    return result


def unresolved_scene_transition(review):
    """An explicitly unresolved scene cannot retain a prior spatial assertion."""
    if not review.get('success'):
        return False
    try:
        confidence = float(review.get('confidence') or 0)
    except (TypeError, ValueError):
        confidence = 0
    return bool((review.get('classification') == 'uncertain' and .75 <= confidence <= 1)
                or (review.get('proposed_classification') in {'external_body', 'trocar_transition'}
                    and review.get('rejected_reason') == 'external_scene_requires_distinct_frame_evidence'
                    and review.get('external_frame_indices')))


def validate_fog_transition(visibility, image_count):
    """Only accept a within-window recovery backed by ordered sample indices."""
    result = normalize_quality_flags(visibility)
    if not result.get('fog_cleared'):
        return result
    def indices(key):
        values = result.get(key)
        return {i for i in values if type(i) is int and 1 <= i <= image_count} if isinstance(values, list) else set()
    fog, clear = indices('fog_frame_indices'), indices('clear_frame_indices')
    valid = (result.get('status') == 'clear' and not result.get('fog')
             and not result.get('out_of_body') and fog and clear
             and max(fog) < min(clear) and image_count in clear)
    if not valid:
        result['fog_cleared'] = False
        result['rejected_recovery_reason'] = 'missing_ordered_fog_to_clear_evidence'
    return result
