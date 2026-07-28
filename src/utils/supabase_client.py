import os
import pytz
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from supabase import create_client, Client

logger = logging.getLogger(__name__)

class SupabaseManager:
    """Supabase 同步后端数据漫游与鉴权服务 (Phase 6 升级)"""
    
    def __init__(self):
        self.url = os.getenv('SUPABASE_URL', '')
        self.key = os.getenv('SUPABASE_KEY', '')
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        if not self._client:
            if not self.url or not self.key:
                raise ValueError("Supabase 配置未设置")
            self._client = create_client(self.url, self.key)
        return self._client

    # ==================== 授权管理 (License Auth) ====================

    async def activate_and_verify_license(self, activation_code: str, machine_code: str) -> Dict[str, Any]:
        """验证并激活授权: 基于同步后端验证机器码和激活码有效性"""
        try:
            current_time = datetime.now()
            # 1. 查找有效激活码
            code_resp = self.client.table('activation_codes').select('*').eq('code', activation_code).eq('status', 'unused').execute()
            if not code_resp.data:
                return {'valid': False, 'message': '无效的或已被使用的激活码'}
                
            code_info = code_resp.data[0]
            valid_days = code_info.get('valid_days', 30)
            
            # 2. 检查设备当前授权状态
            device_resp = self.client.table('device_licenses').select('*').eq('machine_code', machine_code).execute()
            has_active_license = any(
                lic.get('status') == 'active' and 
                datetime.fromisoformat(lic.get('expired_at', '').replace('Z', '+00:00')).astimezone() > current_time.astimezone()
                for lic in device_resp.data
            )
            
            if has_active_license:
                return {'valid': False, 'message': '此设备已有有效的激活授权'}
                
            expired_at = (current_time + timedelta(days=valid_days)).isoformat()
            
            # 3. 更新激活码状态与发放 License
            self.client.table('activation_codes').update({
                'status': 'used',
                'first_activated_at': current_time.isoformat(),
                'updated_at': current_time.isoformat()
            }).eq('id', code_info['id']).execute()
            
            self.client.table('device_licenses').insert({
                'machine_code': machine_code,
                'activation_code_id': code_info['id'],
                'status': 'active',
                'expired_at': expired_at,
                'created_at': current_time.isoformat(),
                'updated_at': current_time.isoformat()
            }).execute()
            
            return {
                'valid': True, 
                'data': {
                    'machine_code': machine_code,
                    'expired_at': expired_at,
                    'license_type': code_info.get('license_type', 'trial')
                }
            }
            
        except Exception as e:
            logger.error(f"激活过程出错: {e}")
            return {'valid': False, 'message': f'验证失败: {str(e)}'}

    async def verify_license(self, machine_code: str) -> Dict[str, Any]:
        """验证已存在的设备授权"""
        try:
            resp = self.client.table('device_licenses').select('*').eq('machine_code', machine_code).execute()
            if not resp.data:
                return {'valid': False, 'message': '设备未激活'}
                
            license_info = resp.data[0]
            if license_info.get('status') != 'active':
                return {'valid': False, 'message': '授权已失效'}
                
            expired_time = datetime.fromisoformat(license_info['expired_at'].replace('Z', '+00:00'))
            if expired_time.astimezone() < datetime.now().astimezone():
                self.client.table('device_licenses').update({'status': 'expired'}).eq('id', license_info['id']).execute()
                return {'valid': False, 'message': '授权已过期'}
                
            return {'valid': True, 'data': license_info}
            
        except Exception as e:
            logger.error(f"验证授权出错: {e}")
            return {'valid': False, 'message': f'验证异常: {str(e)}'}

    async def unbind_license(self, activation_code: str) -> Dict[str, Any]:
        """解绑设备"""
        try:
            code_resp = self.client.table('activation_codes').select('*').eq('code', activation_code).execute()
            if not code_resp.data:
                return {'success': False, 'message': '激活码不存在'}
                
            code_info = code_resp.data[0]
            code_id = code_info['id']
            unbind_remain = code_info.get('unbind_remain', 0)
            
            if unbind_remain <= 0:
                return {'success': False, 'message': '无法解绑，剩余可解绑次数为 0'}
                
            license_resp = self.client.table('device_licenses').select('*').eq('activation_code_id', code_id).execute()
            if not license_resp.data:
                return {'success': False, 'message': '未找到对应的设备授权记录'}
                
            self.client.table('device_licenses').delete().eq('activation_code_id', code_id).execute()
            
            self.client.table('activation_codes').update({
                'status': 'unused',
                'updated_at': datetime.now().isoformat(),
                'unbind_remain': unbind_remain - 1
            }).eq('id', code_id).execute()
            
            return {'success': True, 'message': '解绑成功', 'data': {'remain_unbind': unbind_remain - 1}}
            
        except Exception as e:
            logger.error(f"解绑过程出错: {e}")
            return {'success': False, 'message': f'解绑失败: {str(e)}'}

    # ==================== 代理商后台漫游机制 (Agent Backend) ====================

    async def verify_agent_login(self, name: str, password: str) -> Dict[str, Any]:
        """验证供应商/代理商登录"""
        try:
            query = self.client.table('agents').select('*').eq('name', name)
            response = query.execute()
            if not response.data:
                return {'success': False, 'message': '供应商不存在'}
                
            agent = response.data[0]
            if agent.get('login_password') != password:
                return {'success': False, 'message': '密码错误'}
                
            return {
                'success': True, 
                'data': {
                    'id': agent['id'],
                    'name': agent['name'],
                    'channel_id': agent.get('channel_id'),
                    'brand_name_cn': agent.get('brand_name_cn')
                }
            }
        except Exception as e:
            logger.error(f"代理商登录验证失败: {e}")
            return {'success': False, 'message': f'登录异常: {str(e)}'}

    async def get_agent_activation_codes(self, agent_name: str) -> List[Dict]:
        """拉取指定代理商名下的激活码列表"""
        tz = pytz.timezone('Asia/Shanghai')
        
        def to_cst(dtstr):
            if not dtstr: return None
            dt = datetime.fromisoformat(dtstr.replace('Z', '+00:00'))
            return dt.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')

        try:
            agent_resp = self.client.table('agents').select('id').eq('name', agent_name).execute()
            if not agent_resp.data: return []
            agent_id = agent_resp.data[0]['id']
            
            code_resp = self.client.table('activation_codes').select('*').eq('agent_id', agent_id).order('created_at', desc=True).execute()
            codes = code_resp.data or []
            
            if not codes: return []
            code_ids = [c['id'] for c in codes]
            
            device_resp = self.client.table('device_licenses').select('*').in_('activation_code_id', code_ids).execute()
            device_map = {d['activation_code_id']: d for d in (device_resp.data or [])}
            
            results = []
            for c in codes:
                device = device_map.get(c['id'])
                first_activated_at = c.get('first_activated_at')
                expired_at = device.get('expired_at') if device else None
                
                results.append({
                    'code': c.get('code'),
                    'created_at': to_cst(c.get('created_at')),
                    'updated_at': to_cst(c.get('updated_at')),
                    'status': c.get('status'),
                    'valid_days': c.get('valid_days'),
                    'machine_code': device.get('machine_code') if device else None,
                    'first_activated_at': to_cst(first_activated_at),
                    'expired_at': to_cst(expired_at),
                    'unbind_remain': c.get('unbind_remain'),
                    'remark': c.get('remark')
                })
            return results
        except Exception as e:
            logger.error(f"拉取代理商激活码异常: {e}")
            return []