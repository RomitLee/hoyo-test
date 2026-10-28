import assert from 'node:assert/strict'
import test from 'node:test'
import { sortRolesByServer } from '../src/roleSort.ts'

test('groups roles by server and puts larger server groups first', () => {
  const servers = ['百花村', '聚缘唐城', '钓鱼岛', '罗浮山', '秦淮风光', '罗浮山', '秦淮风光', '秦淮风光']
  const roles = servers.map((server_name, index) => ({ server_name, role_online: true, character_id: String(index) }))
  assert.deepEqual(sortRolesByServer(roles).map(role => role.server_name), [
    '秦淮风光', '秦淮风光', '秦淮风光', '罗浮山', '罗浮山', '百花村', '聚缘唐城', '钓鱼岛',
  ])
  assert.deepEqual(roles.map(role => role.server_name), servers)
})

test('keeps offline roles with their server and puts online roles first within it', () => {
  const roles = [
    { server_name: '秦淮风光', role_online: false, character_id: '1' },
    { server_name: '罗浮山', role_online: true, character_id: '2' },
    { server_name: '秦淮风光', role_online: true, character_id: '3' },
  ]
  assert.deepEqual(sortRolesByServer(roles).map(role => role.character_id), ['3', '1', '2'])
})
