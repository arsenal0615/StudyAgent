"""Build the Codex model catalog for the AIHUB models this deployment can reach.

The AIHUB key from tripo-cli serves gpt-5.1 / gpt-5.2 (Responses API) but not
the newer models in the desktop catalog. Codex 0.164 needs catalog metadata to
avoid sending fields older models reject (reasoning.context=all_turns, custom
tools), so each entry disables those experimental features explicitly.

Usage: python build-model-catalog.py <codex models_cache.json> <output models.json>
The template comes from a desktop Codex 0.164 models_cache.json (gpt-5.6-sol entry);
only its base instructions are kept.
"""
import copy
import json
import sys

MODELS = [
    ('gpt-5.1', 'GPT-5.1', 'AIHUB · 通用推理模型（默认）'),
    ('gpt-5.2', 'GPT-5.2', 'AIHUB · 较新通用推理模型'),
]


def main() -> None:
    cache = json.load(open(sys.argv[1], encoding='utf-8'))
    template = next(model for model in cache['models'] if model['slug'] == 'gpt-5.6-sol')
    models = []
    for priority, (slug, name, description) in enumerate(MODELS, 1):
        model = copy.deepcopy(template)
        model.update(slug=slug, display_name=name, description=description, input_modalities=['text', 'image'], priority=priority,
                     visibility='list', supported_in_api=True, upgrade=None, availability_nux=None, additional_speed_tiers=[],
                     service_tiers=[], supports_experimental_context=False, use_responses_lite=False, experimental_supported_tools=[],
                     multi_agent_version='v1', default_reasoning_level='low',
                     supported_reasoning_levels=[level for level in template['supported_reasoning_levels'] if level['effort'] in ('low', 'medium', 'high')])
        model['model_messages'] = {'instructions_template': template['model_messages']['instructions_template']}
        models.append(model)
    with open(sys.argv[2], 'w', encoding='utf-8') as output:
        json.dump({'models': models}, output, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
