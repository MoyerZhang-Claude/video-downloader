package com.videodl.app

import java.time.LocalDate
import java.time.LocalDateTime

/**
 * 首页问候语（像 Claude 应用那样）：按系统时间、星期几、节日随机挑一句。
 * 和电脑版 app/greetings.py 是同一套规则，改的时候两边一起改。{n} 是称呼，没设置称呼时去掉。
 */
object Greetings {
    private const val WEEK = "一二三四五六日"

    private class Period(val from: Int, val to: Int, val id: String, val texts: List<String>)

    private val periods = listOf(
        Period(0, 5, "midnight", listOf("夜深了，{n}", "还没睡呀，{n}？", "夜猫子，你好呀", "熬夜看视频？早点休息哦，{n}")),
        Period(5, 9, "morning", listOf("早上好，{n}", "早安，{n}", "{n}，今天起得真早", "新的一天，从一杯咖啡开始")),
        Period(9, 11, "forenoon", listOf("上午好，{n}", "早上好，{n}", "{n}，上午好呀")),
        Period(11, 13, "noon", listOf("中午好，{n}", "午饭吃了吗，{n}？", "午休时间到啦，{n}")),
        Period(13, 17, "afternoon", listOf("下午好，{n}", "来杯下午茶吧，{n}", "{n}，下午好呀")),
        Period(17, 19, "dusk", listOf("傍晚好，{n}", "辛苦一天了，{n}", "{n}，忙完了吗？")),
        Period(19, 23, "evening", listOf("晚上好，{n}", "今晚想看点什么，{n}？", "{n}，晚上好呀", "忙完了吗，{n}？")),
        Period(23, 24, "late", listOf("夜深了，{n}", "还没睡呀，{n}？", "{n}，记得早点休息")),
    )

    private val general = listOf("又见面了，{n}", "欢迎回来，{n}", "{n}，今天想下载点什么？", "有什么好看的视频要存吗？", "你好呀，{n}")

    // 固定日期的节日：月、日、天数
    private val fixed = listOf(
        Triple(1 to 1, 1, listOf("新年快乐，{n}", "元旦快乐，{n}")), Triple(2 to 14, 1, listOf("情人节快乐，{n}")),
        Triple(4 to 1, 1, listOf("愚人节快乐，{n}")), Triple(5 to 1, 5, listOf("劳动节快乐，{n}", "五一快乐，{n}")),
        Triple(6 to 1, 1, listOf("儿童节快乐，{n}")), Triple(10 to 1, 7, listOf("国庆快乐，{n}", "国庆假期愉快，{n}")),
        Triple(10 to 31, 1, listOf("万圣节快乐，{n}")), Triple(12 to 24, 1, listOf("平安夜快乐，{n}")),
        Triple(12 to 25, 1, listOf("圣诞快乐，{n}")), Triple(12 to 31, 1, listOf("跨年快乐，{n}", "今年最后一天啦，{n}")),
    )

    // 农历节日（公历日期逐年不同，列出 2026～2030 年）
    private val lunar = mapOf(
        "春节" to listOf("2026-02-17", "2027-02-06", "2028-01-26", "2029-02-13", "2030-02-03"),
        "端午" to listOf("2026-06-19", "2027-06-09", "2028-05-28", "2029-06-16", "2030-06-05"),
        "中秋" to listOf("2026-09-25", "2027-09-15", "2028-10-03", "2029-09-22", "2030-09-12"),
    )
    private val lunarText = mapOf(
        "除夕" to listOf("除夕快乐，{n}", "{n}，过年好"),
        "春节" to listOf("新年快乐，{n}", "{n}，过年好", "春节快乐，{n}"),
        "端午" to listOf("端午安康，{n}"),
        "中秋" to listOf("中秋快乐，{n}", "{n}，月饼吃了吗？"),
    )

    private fun fill(t: String, name: String) =
        if (name.isNotEmpty()) t.replace("{n}", name)
        else t.replace("，{n}", "").replace("{n}，", "").replace("{n}", "").trim('，')

    private fun period(now: LocalDateTime) = periods.first { now.hour >= it.from && now.hour < it.to }

    private fun weekday(now: LocalDateTime): List<String> {
        val d = now.dayOfWeek.value - 1          // 0 = 周一
        val out = mutableListOf("周${WEEK[d]}快乐，{n}", if (d < 6) "星期${WEEK[d]}快乐，{n}" else "星期天快乐，{n}")
        when {
            d == 0 -> out += "新的一周，加油，{n}"
            d == 4 -> out += listOf("周五啦，周末就要来了", "{n}，明天就是周末啦")
            d >= 5 -> out += listOf("周末愉快，{n}", "难得周末，好好放松一下吧")
        }
        return out
    }

    private fun holidays(now: LocalDateTime): List<String> {
        val today = now.toLocalDate()
        val out = mutableListOf<String>()
        for ((md, days, texts) in fixed) {
            val start = LocalDate.of(today.year, md.first, md.second)
            if (!today.isBefore(start) && today.isBefore(start.plusDays(days.toLong()))) out += texts
        }
        for ((fest, dates) in lunar) for (s in dates) {
            val d0 = LocalDate.parse(s)
            if (fest == "春节" && today == d0.minusDays(1)) out += lunarText.getValue("除夕")
            else if (!today.isBefore(d0) && today.isBefore(d0.plusDays(if (fest == "春节") 7 else 1))) out += lunarText.getValue(fest)
        }
        return out
    }

    /** 时间段标识（日期 + 时段）：变了就该换一句。 */
    fun slot(now: LocalDateTime = LocalDateTime.now()) = "${now.toLocalDate()}-${period(now).id}"

    private var last = ""

    fun pick(name: String, now: LocalDateTime = LocalDateTime.now()): String {
        val n = name.trim()
        val holiday = holidays(now)
        val pool = if (holiday.isNotEmpty() && Math.random() < 0.7) holiday
                   else List(3) { period(now).texts }.flatten() + List(2) { weekday(now) }.flatten() + general
        val choices = pool.map { fill(it, n) }.filter { it != last }.ifEmpty { listOf(fill(pool.first(), n)) }
        return choices.random().also { last = it }
    }
}
