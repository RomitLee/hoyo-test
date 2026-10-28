type SortableRole = {
  server_name?: string
  role_online: boolean
  received_at?: string
  first_seen_at?: string
  character_id?: string
}

export function sortRolesByServer<T extends SortableRole>(roles: T[]): T[] {
  const counts = new Map<string, number>()
  const firstSeen = new Map<string, number>()
  for (const [index, role] of roles.entries()) {
    const server = role.server_name?.trim() || ''
    counts.set(server, (counts.get(server) || 0) + 1)
    if (!firstSeen.has(server)) firstSeen.set(server, index)
  }
  return [...roles].sort((a, b) => {
    const aServer = a.server_name?.trim() || ''
    const bServer = b.server_name?.trim() || ''
    return (counts.get(bServer) || 0) - (counts.get(aServer) || 0)
      || (firstSeen.get(aServer) || 0) - (firstSeen.get(bServer) || 0)
      || Number(b.role_online) - Number(a.role_online)
      || (b.received_at || b.first_seen_at || '').localeCompare(a.received_at || a.first_seen_at || '')
      || (a.character_id || '').localeCompare(b.character_id || '')
  })
}
