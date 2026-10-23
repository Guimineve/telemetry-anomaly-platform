import { useCallback, useEffect, useState } from "react";
import { fetchUsers, resetPassword, updateUser } from "../api";
import { shortDateTime } from "../format";
import type { Role, User } from "../types";

export function UsersAdmin({ me }: { me: User }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  const [newPassword, setNewPassword] = useState("");

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

  const reset = async (u: User) => {
    try {
      await resetPassword(u.id, newPassword);
      setError(null);
      setNotice(`Password for ${u.username} was reset and their sessions were logged out.`);
      setResetting(null);
      setNewPassword("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section className="card" aria-labelledby="users-heading">
      <div className="card-head">
        <h2 id="users-heading">Users</h2>
        <span className="count">{users.length}</span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      <div className="table-wrap">
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
                  <td data-label="User">
                    {u.username}
                    {self && <span className="muted"> (you)</span>}
                  </td>
                  <td data-label="Role">
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
                  <td data-label="Last login">
                    {u.lastLoginAt ? shortDateTime(u.lastLoginAt, false) : "never"}
                  </td>
                  <td>
                    {!self && resetting === u.id && (
                      <form
                        className="row-form"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void reset(u);
                        }}
                      >
                        <input
                          type="password"
                          aria-label={`New password for ${u.username}`}
                          autoComplete="new-password"
                          required
                          minLength={10}
                          value={newPassword}
                          onChange={(e) => setNewPassword(e.target.value)}
                        />
                        <button type="submit" className="small">
                          Save
                        </button>
                        <button type="button" className="small" onClick={() => setResetting(null)}>
                          Cancel
                        </button>
                      </form>
                    )}
                    {!self && resetting !== u.id && (
                      <span className="row-actions">
                        <button
                          className="small"
                          onClick={() => {
                            setResetting(u.id);
                            setNewPassword("");
                            setNotice(null);
                          }}
                        >
                          Reset password
                        </button>
                        <button
                          className={u.isActive ? "small danger" : "small"}
                          onClick={() => void change(u.id, { isActive: !u.isActive })}
                        >
                          {u.isActive ? "Disable" : "Enable"}
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
