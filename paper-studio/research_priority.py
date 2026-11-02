"""Explainable reading priority, not a scientific quality or prestige score."""
import re

# A preference list, not a whitelist. Only explicit affiliation metadata counts;
# mentioning another lab's model in an abstract does not establish authorship.
PREFERRED_INSTITUTIONS = (
    'Stanford University', 'Massachusetts Institute of Technology', 'Carnegie Mellon University',
    'University of California, Berkeley', 'University of California, San Diego',
    'University of Oxford', 'University of Cambridge', 'ETH Zurich', 'EPFL',
    'Tsinghua University', 'Peking University', 'Shanghai Jiao Tong University',
    'Zhejiang University', 'Fudan University', 'Nanjing University',
    'University of Science and Technology of China', 'Chinese Academy of Sciences',
    'The University of Hong Kong', 'Hong Kong University of Science and Technology',
    'National University of Singapore', 'Nanyang Technological University',
    'University of Toronto', 'University of Washington', 'Princeton University',
    'Harvard University', 'Cornell University', 'New York University',
    'Google DeepMind', 'Google Research', 'Microsoft Research', 'NVIDIA', 'OpenAI',
    'Anthropic', 'Meta AI', 'Meta FAIR', 'Alibaba', 'ByteDance', 'Tencent',
    'Huawei', 'Baidu', 'DeepSeek', 'Shanghai AI Laboratory', 'Toyota Research Institute',
    'Waymo', 'Tesla', 'Xiaomi', 'Bosch', 'BMW', 'Mercedes-Benz',
)


def priority(item, preferred=PREFERRED_INSTITUTIONS):
    title = item.get('title', '')
    abstract = item.get('abstract', '')
    evidence = item.get('affiliations', [])
    normalized = [' '.join(str(s).casefold().split()) for s in evidence]
    matched = [org for org in preferred if any(
        re.search(r'(?<!\w)' + re.escape(org.casefold()) + r'(?!\w)', a) for a in normalized)]
    score = 10 if item.get('topics') else 0
    reasons = ['与所选研究方向相关'] if score else []
    if len(abstract) >= 300:
        score += 10; reasons.append('有完整摘要可供筛选')
    if re.search(r'propos|introduc|present|develop|design|framework|architect', abstract, re.I):
        score += 10; reasons.append('摘要描述了具体方法或研究问题')
    if re.search(r'experiment|benchmark|evaluat|ablation|latency|throughput|real.world', abstract, re.I):
        score += 15; reasons.append('摘要交代了实验或评测（结果尚待原文核实）')
    if re.search(r'https?://(?:github\.com|[\w.-]+\.github\.io)/', abstract, re.I):
        score += 15; reasons.append('摘要提供代码或项目线索（实现尚待核实）')
    if matched:
        score += 15; reasons.append('明确的作者机构匹配优先关注名单')
    return {'score':score, 'reasons':reasons, 'preferred_institutions':matched,
            'affiliation_status':'metadata' if evidence else 'unknown',
            'note':'检索优先级，不是科学质量评分；机构缺失不会被排除。'}


def ranked(items):
    return sorted(items, key=lambda p:(p.get('priority', priority(p))['score'], p.get('published','')), reverse=True)
