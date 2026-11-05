"""首页问候语（像 Claude 应用那样）：按系统时间、星期几、节日随机挑一句。
安卓版用的是照着这里写的 Greetings.kt（安卓版没有网盘下载，所以没有网盘那一句）。

模板里的 {n} 是称呼；没设置称呼时自动去掉「，{n}」「{n}，」。
"""
import datetime
import random

WEEK = '一二三四五六日'

# 时段：(开始小时, 结束小时, 名字, 模板)
PERIODS = [
    (0, 5, 'midnight', ['夜深了，{n}', '还没睡呀，{n}？', '夜猫子，你好呀', '熬夜看视频？早点休息哦，{n}']),
    (5, 9, 'morning', ['早上好，{n}', '早安，{n}', '{n}，今天起得真早', '新的一天，从一杯咖啡开始']),
    (9, 11, 'forenoon', ['上午好，{n}', '早上好，{n}', '{n}，上午好呀']),
    (11, 13, 'noon', ['中午好，{n}', '午饭吃了吗，{n}？', '午休时间到啦，{n}']),
    (13, 17, 'afternoon', ['下午好，{n}', '来杯下午茶吧，{n}', '{n}，下午好呀']),
    (17, 19, 'dusk', ['傍晚好，{n}', '辛苦一天了，{n}', '{n}，忙完了吗？']),
    (19, 23, 'evening', ['晚上好，{n}', '今晚想看点什么，{n}？', '{n}，晚上好呀', '忙完了吗，{n}？']),
    (23, 24, 'late', ['夜深了，{n}', '还没睡呀，{n}？', '{n}，记得早点休息']),
]

GENERAL = ['又见面了，{n}', '欢迎回来，{n}', '{n}，今天想下载点什么？', '有什么好看的视频要存吗？', '你好呀，{n}',
           '网盘里有什么要搬下来的？']

# 固定日期的节日：(月, 日, 天数, 模板)
FIXED_HOLIDAYS = [
    (1, 1, 1, ['新年快乐，{n}', '元旦快乐，{n}']), (2, 14, 1, ['情人节快乐，{n}']), (4, 1, 1, ['愚人节快乐，{n}']),
    (5, 1, 5, ['劳动节快乐，{n}', '五一快乐，{n}']), (6, 1, 1, ['儿童节快乐，{n}']),
    (10, 1, 7, ['国庆快乐，{n}', '国庆假期愉快，{n}']), (10, 31, 1, ['万圣节快乐，{n}']),
    (12, 24, 1, ['平安夜快乐，{n}']), (12, 25, 1, ['圣诞快乐，{n}']), (12, 31, 1, ['跨年快乐，{n}', '今年最后一天啦，{n}']),
]

# 农历节日（公历日期逐年不同，列出 2026～2030 年）
LUNAR_HOLIDAYS = {
    '春节': ['2026-02-17', '2027-02-06', '2028-01-26', '2029-02-13', '2030-02-03'],
    '端午': ['2026-06-19', '2027-06-09', '2028-05-28', '2029-06-16', '2030-06-05'],
    '中秋': ['2026-09-25', '2027-09-15', '2028-10-03', '2029-09-22', '2030-09-12'],
}
LUNAR_TEXT = {
    '除夕': ['除夕快乐，{n}', '{n}，过年好'],
    '春节': ['新年快乐，{n}', '{n}，过年好', '春节快乐，{n}'],
    '端午': ['端午安康，{n}'],
    '中秋': ['中秋快乐，{n}', '{n}，月饼吃了吗？'],
}


def _fill(template, name):
    if name:
        return template.replace('{n}', name)
    return template.replace('，{n}', '').replace('{n}，', '').replace('{n}', '').strip('，')


def _weekday_templates(now):
    d = now.weekday()
    out = [f'周{WEEK[d]}快乐，{{n}}', f'星期{WEEK[d]}快乐，{{n}}' if d < 6 else '星期天快乐，{n}']
    if d == 0:
        out.append('新的一周，加油，{n}')
    elif d == 4:
        out += ['周五啦，周末就要来了', '{n}，明天就是周末啦']
    elif d >= 5:
        out += ['周末愉快，{n}', '难得周末，好好放松一下吧']
    return out


def _holiday_templates(now):
    today = now.date()
    out = []
    for m, day, length, texts in FIXED_HOLIDAYS:
        start = datetime.date(today.year, m, day)
        if start <= today < start + datetime.timedelta(days=length):
            out += texts
    for fest, dates in LUNAR_HOLIDAYS.items():
        for s in dates:
            d0 = datetime.date.fromisoformat(s)
            if fest == '春节' and today == d0 - datetime.timedelta(days=1):
                out += LUNAR_TEXT['除夕']
            elif d0 <= today < d0 + datetime.timedelta(days=7 if fest == '春节' else 1):
                out += LUNAR_TEXT[fest]
    return out


def period_of(now=None):
    now = now or datetime.datetime.now()
    return next(p for p in PERIODS if p[0] <= now.hour < p[1])


def slot(now=None):
    """问候语的「时间段标识」：日期 + 时段，变了就该换一句。"""
    now = now or datetime.datetime.now()
    return f'{now:%Y-%m-%d}-{period_of(now)[2]}'


_last = {'text': ''}


def greeting(name='', now=None):
    """随机挑一句问候语：节日当天优先说节日；平时按时段、星期几和一些日常问候混着来，不和上一句重复。"""
    now = now or datetime.datetime.now()
    name = (name or '').strip()
    holiday = _holiday_templates(now)
    if holiday and random.random() < 0.7:
        pool = holiday
    else:
        pool = period_of(now)[3] * 3 + _weekday_templates(now) * 2 + GENERAL
    choices = [t for t in (_fill(x, name) for x in pool) if t != _last['text']] or [_fill(pool[0], name)]
    text = random.choice(choices)
    _last['text'] = text
    return text


if __name__ == '__main__':      # 预览：python -m app.greetings
    for h in (1, 7, 10, 12, 15, 18, 21, 23):
        t = datetime.datetime(2026, 11, 3, h)
        print(h, [greeting('Moyer', t) for _ in range(4)], greeting('', t))
    print('国庆', greeting('Moyer', datetime.datetime(2026, 10, 3, 20)))
    print('中秋', greeting('', datetime.datetime(2026, 9, 25, 20)))
