import json

from django import template


register = template.Library()


@register.filter
def json_value(value):
    return json.dumps(value or {}, ensure_ascii=False, indent=2)
