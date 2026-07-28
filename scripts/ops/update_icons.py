import json
import ast

def run():
    with open('src/crm/industry_config.py', 'r', encoding='utf-8') as f:
        source = f.read()

    # Find the `SYSTEM_TEMPLATES` definition
    tree = ast.parse(source)
    templates_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'SYSTEM_TEMPLATES':
                    templates_node = node.value

    if not templates_node:
        print("not found")
        return

    templates_code = ast.unparse(templates_node)
    templates = eval(templates_code)

    icon_mapping = {
        '🌐': 'i-carbon-earth-filled text-blue-500', 
        '🚀': 'i-carbon-rocket text-purple-600',
        '☁️': 'i-carbon-cloud text-sky-400',
        '🖥️': 'i-carbon-screen text-indigo-500',
        '🎮': 'i-carbon-game-console text-orange-500',
        '🏢': 'i-carbon-building-insights-1 text-emerald-600',
        '🛋️': 'i-carbon-model-alt text-amber-700',
        '🏬': 'i-carbon-store text-cyan-500',
        '🚗': 'i-carbon-car text-red-500',
        '🔧': 'i-carbon-tool-kit text-gray-500',
        '🚦': 'i-carbon-pedestrian text-green-500',
        '✨': 'i-carbon-magic-wand text-pink-500',
        '🦷': 'i-carbon-face-activated-add text-blue-400',
        '🌿': 'i-carbon-botanical text-green-600',
        '👁️': 'i-carbon-view text-teal-500',
        '💊': 'i-carbon-pills text-rose-500',
        '🥘': 'i-carbon-restaurant text-orange-600',
        '🌾': 'i-carbon-wheat text-yellow-600',
        '🍰': 'i-carbon-cafe text-amber-500',
        '🎓': 'i-carbon-education text-blue-600',
        '📚': 'i-carbon-notebook text-cyan-600',
        '🌍': 'i-carbon-globe text-blue-400',
        '📋': 'i-carbon-report text-gray-600',
        '🧒': 'i-carbon-user-speaker text-pink-400',
        '💰': 'i-carbon-currency text-yellow-500',
        '📈': 'i-carbon-chart-line text-green-500',
        '💳': 'i-carbon-purchase text-blue-500',
        '⚖️': 'i-carbon-scales text-slate-700',
        '📝': 'i-carbon-document-signed text-gray-600',
        '💅': 'i-carbon-color-palette text-fuchsia-500',
        '💍': 'i-carbon-gem text-purple-400',
        '👔': 'i-carbon-user-avatar-filled-alt text-slate-800',
        '🍼': 'i-carbon-face-satisfied text-pink-300',
        '🐶': 'i-carbon-dog-walker text-amber-600',
        '🧹': 'i-carbon-clean text-teal-600',
        '📦': 'i-carbon-delivery-parcel text-orange-400',
        '🔌': 'i-carbon-plug text-gray-700',
        '🏋️': 'i-carbon-activity text-red-600',
        '🏆': 'i-carbon-trophy text-yellow-500',
        '⛺': 'i-carbon-map text-green-700',
        '✈️': 'i-carbon-plane text-sky-500',
        '🏨': 'i-carbon-hotel text-indigo-400',
        '🎬': 'i-carbon-video text-red-500',
        '📱': 'i-carbon-mobile text-slate-800',
        '🎨': 'i-carbon-paint-brush text-fuchsia-600',
        '🚚': 'i-carbon-delivery-truck text-cyan-700',
        '🚢': 'i-carbon-boat text-blue-700',
        '🧾': 'i-carbon-receipt text-gray-500',
        '⌚': 'i-carbon-watch text-slate-600',
        '💎': 'i-carbon-diamond text-cyan-400',
        '🏺': 'i-carbon-pedestrian-family text-amber-800',
        '⚙️': 'i-carbon-settings text-gray-600',
        '📹': 'i-carbon-video-filled text-blue-900',
        '🧪': 'i-carbon-chemistry text-purple-500',
        '🧵': 'i-carbon-machine-learning-model text-emerald-500',
        '☀️': 'i-carbon-sun text-orange-400',
        '♻️': 'i-carbon-recycle text-green-500',
        '🪷': 'i-carbon-flora text-pink-400',
        '👷': 'i-carbon-user-certification text-yellow-600',
        '🚜': 'i-carbon-agriculture-analytics text-green-600',
        '🐄': 'i-carbon-qq text-blue-200', 
        '🕯️': 'i-carbon-fire text-orange-500',
        '❤️': 'i-carbon-favorite-filled text-red-500',
        '🤱': 'i-carbon-user-favorite-alt text-pink-500',
        '🌹': 'i-carbon-flower text-rose-600',
    }

    for t in templates:
        old_icon = t['icon']
        if old_icon in icon_mapping:
            t['icon'] = icon_mapping[old_icon]
        else:
            t['icon'] = 'i-carbon-information-square text-gray-500' 
            # fallback icon

    templates_str = "SYSTEM_TEMPLATES = [\n"
    for d in templates:
        templates_str += f"    {repr(d)},\n"
    templates_str += "]\n"
    
    start_idx = source.find("SYSTEM_TEMPLATES = [")
    end_idx = source.find("class IndustryConfigManager:")
    
    new_source = source[:start_idx] + templates_str + "\n\n" + source[end_idx:]
    with open('src/crm/industry_config.py', 'w', encoding='utf-8') as f:
        f.write(new_source)
    print("Icons replaced!")

if __name__ == '__main__':
    run()
