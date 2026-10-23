import { useCallback, useEffect, useState } from "react";
import { fetchUsers, updateUser } from "../api";
import type { Role, User } from "../types";

export function UsersAdmin({ me }: { me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchUsers().then(setUsers, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const change = async (id: string, patch: { role?: Role; isActive?: boolean }) => {
    try {
      await updateUser(id, patch);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
    load();
  };

  return (
    <section aria-labelledby="users-heading">
      <h2 id="users-heading">Users</h2>
      {error && <p role="alert" className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Username</th>
            <th>Role</th>
            <th>Last login</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const self = u.id === me.id;
            return (
              <tr key={u.id} className={u.isActive ? "" : "disabled"}>
                <td>
                  {u.username}
                  {self && <span className="muted"> (you)</span>}
                </td>
                <td>
                  <select
                    aria-label={`Role of ${u.username}`}
                    value={u.role}
                    disabled={self}
                    onChange={(e) => void change(u.id, { role: e.target.value as Role })}
                  >
                    <option value="viewer">viewer</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td>{u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString() : "never"}</td>
                <td>
                  {!self && (
                    <button onClick={() => void change(u.id, { isActive: !u.isActive })}>
                      {u.isActive ? "Disable" : "Enable"}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
