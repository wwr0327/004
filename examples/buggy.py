"""故意包含缺陷，仅供静态审查演示。请勿用于生产。"""


def add_item(item, items=[]):
    items.append(item)
    return items


def parse_input(user_input):
    return eval(user_input)


def calculate_total(value):
    try:
        return value / 0
    except:
        return None
