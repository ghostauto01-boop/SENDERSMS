import { useState, useEffect } from "react";
import api from "../api/client";
import { Contact } from "../types";
import toast from "react-hot-toast";
import { Plus, Trash2, Users, Edit2, Eye, UserPlus, X, Search } from "lucide-react";

interface ListItem {
  id: number;
  name: string;
  description: string | null;
  contact_count: number;
  created_at: string;
  updated_at: string;
}

export default function ListsPage() {
  const [lists, setLists] = useState<ListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [viewListId, setViewListId] = useState<number | null>(null);
  const [viewListName, setViewListName] = useState("");
  const [listContacts, setListContacts] = useState<Contact[]>([]);
  const [listTotal, setListTotal] = useState(0);
  const [showAddContacts, setShowAddContacts] = useState(false);
  const [allContacts, setAllContacts] = useState<Contact[]>([]);
  const [selectedToAdd, setSelectedToAdd] = useState<Set<number>>(new Set());
  const [addSearch, setAddSearch] = useState("");

  useEffect(() => { loadLists(); }, []);

  const loadLists = async () => {
    try {
      setLoading(true);
      const { data } = await api.get("/lists/");
      setLists(data.items);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to load lists");
    } finally {
      setLoading(false);
    }
  };

  const loadListContacts = async (listId: number) => {
    try {
      const { data } = await api.get(`/lists/${listId}/contacts`, { params: { per_page: 100 } });
      setListContacts(data.items);
      setListTotal(data.total);
    } catch { toast.error("Failed to load contacts"); }
  };

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await api.post("/lists/", null, { params: { name: newName } });
      toast.success("List created");
      setNewName("");
      setShowCreate(false);
      loadLists();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || "Failed to create list");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("Delete this list?")) return;
    try {
      await api.delete(`/lists/${id}`);
      toast.success("List deleted");
      if (viewListId === id) setViewListId(null);
      loadLists();
    } catch { toast.error("Failed to delete list"); }
  };

  const handleRename = async (id: number) => {
    if (!editName.trim()) return;
    try {
      await api.put(`/lists/${id}`, null, { params: { name: editName } });
      toast.success("List renamed");
      setEditingId(null);
      loadLists();
    } catch { toast.error("Failed to rename"); }
  };

  const handleView = async (list: ListItem) => {
    setViewListId(list.id);
    setViewListName(list.name);
    await loadListContacts(list.id);
  };

  const handleAddContacts = async () => {
    if (selectedToAdd.size === 0) return;
    try {
      await api.post(`/lists/${viewListId}/contacts`, [...selectedToAdd]);
      toast.success(`${selectedToAdd.size} contacts added to list`);
      setSelectedToAdd(new Set());
      setShowAddContacts(false);
      loadListContacts(viewListId!);
      loadLists();
    } catch { toast.error("Failed to add contacts"); }
  };

  const handleRemoveContact = async (contactId: number) => {
    try {
      await api.post(`/lists/${viewListId}/contacts/remove`, { contact_ids: [contactId] });
      toast.success("Removed from list");
      loadListContacts(viewListId!);
      loadLists();
    } catch { toast.error("Failed to remove"); }
  };

  const openAddContacts = async () => {
    try {
      const { data } = await api.get("/contacts/", { params: { per_page: 200 } });
      setAllContacts(data.items);
      setShowAddContacts(true);
    } catch { toast.error("Failed to load contacts"); }
  };

  if (error) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">Error</h2>
        <p className="text-gray-500 mb-4">{error}</p>
        <button onClick={loadLists} className="btn-primary">Retry</button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Contact Lists</h1>
        <button onClick={() => setShowCreate(true)} className="btn-primary btn-sm">
          <Plus size={14} className="mr-1" /> Create List
        </button>
      </div>

      {/* Create bar */}
      {showCreate && (
        <div className="card p-4 flex gap-2">
          <input className="input flex-1" placeholder="List name..." value={newName}
            onChange={(e) => setNewName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleCreate()} autoFocus />
          <button onClick={handleCreate} className="btn-primary">Create</button>
          <button onClick={() => setShowCreate(false)} className="btn-secondary">Cancel</button>
        </div>
      )}

      {/* View contacts modal */}
      {viewListId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">{viewListName} — {listTotal} contacts</h2>
              <div className="flex gap-2">
                <button onClick={openAddContacts} className="btn-primary btn-sm"><UserPlus size={14} className="mr-1" /> Add Contacts</button>
                <button onClick={() => setViewListId(null)} className="btn-ghost btn-sm"><X size={16} /></button>
              </div>
            </div>

            {showAddContacts && (
              <div className="mb-4 border rounded-lg p-3 space-y-2">
                <div className="relative">
                  <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
                  <input className="input pl-8 py-1 text-sm" placeholder="Filter contacts..." value={addSearch}
                    onChange={e => setAddSearch(e.target.value)} />
                </div>
                <div className="max-h-48 overflow-y-auto space-y-1">
                  {allContacts.filter(c => !addSearch || c.phone_number.includes(addSearch) || (c.first_name || "").toLowerCase().includes(addSearch.toLowerCase())).slice(0, 100).map(c => (
                    <label key={c.id} className={`flex items-center gap-2 p-1.5 rounded cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 ${selectedToAdd.has(c.id) ? "bg-primary-50 dark:bg-primary-900/20" : ""}`}>
                      <input type="checkbox" checked={selectedToAdd.has(c.id)}
                        onChange={() => {
                          const next = new Set(selectedToAdd);
                          next.has(c.id) ? next.delete(c.id) : next.add(c.id);
                          setSelectedToAdd(next);
                        }} className="rounded" />
                      <span className="text-sm">{c.first_name} {c.last_name} — {c.phone_number}</span>
                    </label>
                  ))}
                </div>
                <button onClick={handleAddContacts} disabled={selectedToAdd.size === 0} className="btn-primary btn-sm">
                  Add {selectedToAdd.size} contact{selectedToAdd.size !== 1 ? "s" : ""}
                </button>
              </div>
            )}

            <div className="space-y-1">
              {listContacts.length === 0 ? (
                <p className="text-center text-gray-500 py-4">No contacts in this list</p>
              ) : (
                listContacts.map(c => (
                  <div key={c.id} className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded">
                    <div>
                      <span className="font-medium text-sm">{c.first_name} {c.last_name}</span>
                      <span className="text-xs text-gray-400 ml-2">{c.phone_number}</span>
                      {c.business_name && <span className="text-xs text-gray-400 ml-2">— {c.business_name}</span>}
                    </div>
                    <button onClick={() => handleRemoveContact(c.id)} className="btn-ghost btn-sm text-red-600"><Trash2 size={14} /></button>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* List cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {loading ? (
          [...Array(6)].map((_, i) => (
            <div key={i} className="card p-4"><div className="skeleton h-5 w-32 mb-2" /><div className="skeleton h-4 w-20" /></div>
          ))
        ) : lists.length === 0 ? (
          <div className="col-span-full text-center py-12 text-gray-500">
            <Users size={48} className="mx-auto mb-2 opacity-30" />
            <p>No lists yet. Create your first contact list.</p>
          </div>
        ) : (
          lists.map((list) => (
            <div key={list.id} className="card p-4 hover:shadow-md transition-shadow">
              {editingId === list.id ? (
                <div className="flex gap-2 mb-2">
                  <input className="input flex-1 text-sm" value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleRename(list.id)} autoFocus />
                  <button onClick={() => handleRename(list.id)} className="btn-primary btn-sm">Save</button>
                  <button onClick={() => setEditingId(null)} className="btn-secondary btn-sm">Cancel</button>
                </div>
              ) : (
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold">{list.name}</h3>
                    {list.description && <p className="text-sm text-gray-500 mt-1">{list.description}</p>}
                  </div>
                  <div className="flex gap-1">
                    <button onClick={() => { setEditingId(list.id); setEditName(list.name); }} className="btn-ghost btn-sm" title="Rename"><Edit2 size={14} /></button>
                    <button onClick={() => handleView(list)} className="btn-ghost btn-sm text-primary-600" title="View Contacts"><Eye size={14} /></button>
                    <button onClick={() => handleDelete(list.id)} className="btn-ghost btn-sm text-red-600" title="Delete"><Trash2 size={14} /></button>
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2 mt-3 text-sm text-gray-500">
                <Users size={14} />
                <span>{list.contact_count} contacts</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
