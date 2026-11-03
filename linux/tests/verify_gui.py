"""Verify real GUI evidence against the unchanged product localization catalogs."""
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[2]
evidence = pathlib.Path(sys.argv[1])
for language in ('en', 'es', 'fr', 'de', 'it', 'pt-BR'):
    result = json.loads((evidence / f'ui-{language}.json').read_text())
    catalog = json.loads((root / 'src/Localization' / f'ui.{language}.json').read_text(encoding='utf-8-sig'))
    assert result['preview'] is True and result['physical_device_opened'] is False
    assert result['ready'] is False
    assert result['headline'] == catalog['Ui.HeroPaused']
    assert result['subheadline'] == catalog['Ui.HeroReleased']
    assert result['tagline'] == catalog['Ui.HeaderTagline']
    assert result['client_width'] >= 800 and result['client_height'] >= 700
    assert (evidence / f'ui-{language}.png').stat().st_size > 10000
    assert 'Linux' in result['platform'] or 'Unix' in result['platform']
print('Six real Linux GUI launches retain branding and remain disconnected.')
