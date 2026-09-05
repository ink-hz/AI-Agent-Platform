import type { HrPanoramaSource } from "../../hrPanoramaTypes";

export interface HrPanoramaCatalogCompany {
  canonicalName: string;
  aliases: string[];
  approvedUrls: string[];
  sourceNote: string;
}

export const HR_PANORAMA_COMPANY_CATALOG: HrPanoramaCatalogCompany[] = [
  { canonicalName: "联合光电", aliases: ["中山联合光电", "Union Optech"], approvedUrls: ["https://www.union-optech.com"], sourceNote: "公司官网；招聘入口待持续核验" },
  { canonicalName: "速腾聚创", aliases: ["RoboSense", "深圳市速腾聚创科技有限公司"], approvedUrls: ["https://www.robosense.ai/en/about/social", "https://www.robosense.ai/en/about/campus"], sourceNote: "官方社招与校招" },
  { canonicalName: "禾赛科技", aliases: ["禾赛", "Hesai"], approvedUrls: ["https://www.hesaitech.com/cn/careers", "https://kwh0jtf778.jobs.feishu.cn/index", "https://kwh0jtf778.jobs.feishu.cn/229043", "https://kwh0jtf778.jobs.feishu.cn/073183"], sourceNote: "官网链接的社招、校招与实习入口" },
  { canonicalName: "拓竹", aliases: ["拓竹科技", "Bambu Lab", "深圳拓竹科技有限公司"], approvedUrls: ["https://bambulab.cn/zh-cn/join-us", "https://bambulab.jobs.feishu.cn/experienced", "https://bambulab.jobs.feishu.cn/campus"], sourceNote: "官网链接的社招与校招入口" },
  { canonicalName: "创想三维", aliases: ["Creality", "深圳市创想三维科技股份有限公司"], approvedUrls: ["https://www.creality.cn/about", "https://www.creality.com/resources/careers", "https://creality.zhiye.com/"], sourceNote: "公司官网、官方招聘页与招聘系统" },
  { canonicalName: "智能派", aliases: ["深圳市智能派科技有限公司", "ELEGOO", "爱乐酷"], approvedUrls: ["https://www.elegoo.com.cn/index/join/index.html", "https://www.zhaopin.com/companydetail/CZ331690680.htm"], sourceNote: "公司招聘页；公开招聘平台交叉核验" },
  { canonicalName: "知象光电", aliases: ["西安知象光电科技有限公司", "Revopoint"], approvedUrls: ["https://hr.revopoint3d.com.cn/", "https://hr.revopoint3d.com.cn/gwtd.html", "https://hr.revopoint3d.com.cn/gwtd1.html", "https://www.revopoint3d.com"], sourceNote: "官方招聘主页、校招、社招与公司官网" },
  { canonicalName: "先临三维", aliases: ["SHINING 3D", "先临三维科技股份有限公司"], approvedUrls: ["https://www.shining3d.com/teams", "https://www.shining3d.cn/t/%E5%85%88%E4%B8%B4%E4%B8%89%E7%BB%B4%E4%BA%BA%E6%89%8D%E6%8B%9B%E8%81%98.html", "https://shining3d.zhiye.com/", "https://shining3d.zhiye.com/campus"], sourceNote: "官方团队页、招聘系统与校招入口" },
  { canonicalName: "思看科技", aliases: ["SCANTECH", "思看科技（杭州）股份有限公司"], approvedUrls: ["https://www.3d-scantech.com.cn/corporation/join-us/", "https://jobs.scantech.cn/"], sourceNote: "公司招聘页与官方岗位系统；校招待持续核验" },
  { canonicalName: "智元机器人", aliases: ["智元", "AGIBOT", "智元创新"], approvedUrls: ["https://www.agibot.com.cn/join_us", "https://agirobot.jobs.feishu.cn/socialrecruitment", "https://agirobot.jobs.feishu.cn/campusrecruitment", "https://agirobot.jobs.feishu.cn/internrecruitment"], sourceNote: "官网链接的社招、校招与实习入口" },
];

export const HR_PANORAMA_SESSION_LEADS = ["影石", "华为"] as const;

function companyNames(source: HrPanoramaSource): Set<string> {
  return new Set([source.canonicalName, ...source.aliases].map((value) => value.trim().toLocaleLowerCase("zh-CN")));
}

export function catalogSource(company: HrPanoramaCatalogCompany, sources: HrPanoramaSource[]): HrPanoramaSource | undefined {
  const expected = new Set([company.canonicalName, ...company.aliases].map((value) => value.trim().toLocaleLowerCase("zh-CN")));
  return sources.find((source) => [...companyNames(source)].some((name) => expected.has(name)));
}

function normalizedUrl(value: string): string { return value.replace(/\/$/, ""); }

export function catalogSourceNeedsUpdate(company: HrPanoramaCatalogCompany, source: HrPanoramaSource): boolean {
  const currentUrls = new Set(source.approvedUrls.map(normalizedUrl));
  const currentAliases = companyNames(source);
  return company.approvedUrls.some((url) => !currentUrls.has(normalizedUrl(url)))
    || company.aliases.some((alias) => !currentAliases.has(alias.trim().toLocaleLowerCase("zh-CN")));
}
