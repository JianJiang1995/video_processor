#!/usr/bin/env python3
"""Find contradictory saved claims, not establish visual or clinical accuracy."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.clip_review import confirmed_empty_tool_inventory, detector_instruments
from backend.services.visibility_consistency import unresolved_scene_transition, fog_quality_disagreement
from backend.services.local_visual_prompt import BAG_TERMS, bag_geometry_disputed


def audit(rows):
    conflicts, review_candidates = [], []
    states, scenes, summaries = Counter(), Counter(), Counter()
    raw_r1_without_detector = []
    for row in sorted(rows, key=lambda item: item['window_id']):
        wid = row['window_id'] + 1
        text = str(row.get('summary') or row.get('glm_summary') or '')
        others = row.get('others') or {}
        visual = others.get('visual_gpt') or {}
        visibility = visual.get('visibility') or {}
        expert = others.get('experts') or {}
        states[str(visibility.get('status', 'missing'))] += 1
        scenes[str((visual.get('scene') or {}).get('classification', 'missing'))] += 1
        summaries[text] += 1
        def flag(kind, target=conflicts):
            target.append({'ui_window': wid, 'kind': kind, 'summary': text})
        if not str(row.get('summary') or '').strip():
            flag('empty_final_summary')
        if fog_quality_disagreement(visual, visual.get('visibility_secondary_review') or {}):
            flag('fog_conflicts_with_quality_review')
        if bag_geometry_disputed(visual) and re.search(BAG_TERMS, text, re.IGNORECASE):
            flag('bag_claim_despite_woven_evidence')
        fog = visibility.get('fog') or visibility.get('status') == 'foggy'
        outside = visibility.get('out_of_body') or visibility.get('status') == 'out_of_body'
        recovery = visibility.get('fog_cleared')
        recovery_text = bool(re.search(r'雾已去除|雾已消散|雾气消失|腹腔视野恢复', text))
        if fog and (recovery or recovery_text):
            flag('active_fog_and_recovery')
        if outside and (fog or recovery):
            flag('outside_and_intra_abdominal_fog_state')
        if outside and re.search(r'(?:电凝钩|抓钳|剪刀|施夹器).{0,15}(?:胆囊|肝脏|脂肪|腹膜)', text):
            flag('outside_with_abdominal_action', review_candidates)
        if unresolved_scene_transition(visual.get('visibility_secondary_review') or {}) and re.search(
                r'镜头移出体外|体外场景|(?:电凝钩|抓钳|剪刀|施夹器).{0,12}(?:接触|牵拉|夹持|分离)', text):
            flag('unconfirmed_scene_with_confirmed_scene_or_action')
        if confirmed_empty_tool_inventory(visual, expert) and re.search(
                r'电凝钩|抓钳|剪刀|施夹器|未知器械|接触|牵拉|夹持|分离|切开|切断|剪断|解剖|凝血|电凝|吸引|冲洗|施夹', text):
            flag('tool_claim_after_empty_inventory_confirmation')
        if detector_instruments(expert) == set() and (expert.get('surgr1') or {}).get('observations'):
            raw_r1_without_detector.append(wid)
        for label, name in [('cystic_duct', '胆囊管'), ('cystic_artery', '胆囊动脉')]:
            rejected = (visual.get('unconfirmed_target_structure') or {}).get('label') == label
            if rejected and re.search(r'(?:分离|牵拉|夹闭|切断|剪断)' + name, text):
                flag('rejected_anatomy_reintroduced')
        if re.search(r'脂肪或粘连|胆囊管或|胆囊管/|胆囊动脉或', text):
            flag('unresolved_alternative_anatomy', review_candidates)
    return {'scope': 'Text/structured-state consistency only; image review is still required',
            'window_count': len(rows), 'conflicts': conflicts,
            'review_candidates': review_candidates, 'visibility_states': dict(states),
            'scene_counts': dict(scenes), 'raw_r1_with_empty_detector_windows': raw_r1_without_detector,
            'most_repeated_summaries': summaries.most_common(8)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='verification.json or summaries.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    result = audit(data.get('rows', []) if isinstance(data, dict) else data)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    (args.output or args.input.with_name('summary_consistency_audit.json')).write_text(rendered)
    print(rendered)
