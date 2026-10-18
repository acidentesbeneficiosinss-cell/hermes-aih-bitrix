"""FakeBitrix: simula, sem rede, o que importa do portal real (comportamentos validados ao vivo em 2026-10-18).

Reproduz: filtros por igualdade (o CPF só casa no valor FORMATADO), o BP #425 (Title Case em nome/mãe),
os robôs do card (título reescrito, etapa por rodízio), a expansão de telefones em variantes e a
semântica de multicampo. Qualquer método `*.delete` é proibido e derruba o teste.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aih_bitrix.bitrix.client import BitrixError
from aih_bitrix.parser import fold

FIXTURES = Path(__file__).parent / "fixtures"
MOTHER_KEY = "UF_CRM_1729515037979"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


class FakeBitrix:
    host = "fake.bitrix24.com.br"

    def __init__(self, *, bp: bool = True, robots: bool = True, phone_variants: bool = True) -> None:
        meta = json.loads((FIXTURES / "portal_meta.json").read_text(encoding="utf-8"))
        self.contact_fields: dict[str, Any] = meta["contact_fields"]
        self.item_fields: dict[str, Any] = meta["item_fields"]
        self.categories: list[dict[str, Any]] = meta["categories"]
        self.stages: list[dict[str, Any]] = meta["stages"]
        self.bp, self.robots, self.phone_variants = bp, robots, phone_variants
        self.contacts: dict[int, dict[str, Any]] = {}
        self.items: dict[int, dict[str, Any]] = {}
        self.comments: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[str, bytes] = {}  # URL de download -> bytes
        self.disk: dict[int, dict[str, Any]] = {}  # id do disco -> objeto de arquivo
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.users: dict[int, dict[str, Any]] = {
            152247: {"id": 152247, "bot": False, "connector": True},  # participante do grupo (conector WhatsApp)
            151851: {"id": 151851, "bot": True, "connector": False},  # "Triagem de Documentos Bot"
        }
        self.download_error: BitrixError | None = None
        self.profile: dict[str, Any] = {"ID": "1", "ADMIN": True}
        self.scopes: list[str] = ["crm", "im", "imopenlines", "disk", "bizproc", "user"]
        self._ids ={"contact": 70000, "item": 3000, "comment": 9000000, "phone": 1300000, "message": 25400000, "disk": 2527000}
        self._round_robin = 0

    # ------------------------------------------------------------------ interface do BitrixClient
    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = copy.deepcopy(params or {})
        self.calls.append((method, params))
        if method.endswith(".delete"):
            raise AssertionError(f"o app NUNCA pode chamar {method}")
        handler = getattr(self, "_m_" + method.replace(".", "_"), None)
        if handler is None:
            raise BitrixError("ERROR_METHOD_NOT_FOUND", method)
        return handler(params)

    def result(self, method: str, params: dict[str, Any] | None = None) -> Any:
        return self.call(method, params).get("result")

    def list_all(self, method: str, params: dict[str, Any], key: str | None = None, limit: int = 2000) -> list[Any]:
        result = self.call(method, {**params, "start": 0}).get("result")
        chunk = result.get(key) if key and isinstance(result, dict) else result
        return list(chunk or [])[:limit]

    def download(self, url: str, max_bytes: int) -> bytes:
        if self.download_error is not None:
            raise self.download_error
        if urlparse(url).netloc.lower() != self.host:
            raise BitrixError("DOWNLOAD_HOST", "URL de download fora do portal Bitrix configurado")
        if url not in self.files:
            raise BitrixError("HTTP_404", "arquivo inexistente no fake")
        data = self.files[url]
        if len(data) > max_bytes:
            raise BitrixError("TOO_LARGE", "maior que o limite")
        return data

    # ------------------------------------------------------------------ helpers de teste
    def _new_id(self, kind: str) -> int:
        self._ids[kind] += 1
        return self._ids[kind]

    def seed_contact(self, **fields: Any) -> int:
        """Cria um contato diretamente (sem passar pelo BP), como se já existisse no portal."""
        cid = self._new_id("contact")
        contact: dict[str, Any] = {"ID": str(cid), "NAME": "", "LAST_NAME": "", "BIRTHDATE": "", "PHONE": []}
        self._apply_contact(contact, fields)
        self.contacts[cid] = contact
        return cid

    def seed_item(self, **fields: Any) -> int:
        iid = self._new_id("item")
        item = {"id": iid, "title": "", "xmlId": "", "categoryId": 101, "stageId": self.stages[0]["STATUS_ID"],
                "contactId": None, "opened": "Y", "assignedById": 1, "createdBy": 1}
        item.update(fields)
        self.items[iid] = item
        return iid

    def add_message(
        self,
        dialog: str,
        *,
        author: int = 152247,
        text: str = "",
        files: tuple[tuple[str, bytes], ...] = (),
        at: datetime | None = None,
    ) -> int:
        """Adiciona uma mensagem ao diálogo (formato observado em im.dialog.messages.get no portal real)."""
        message_id = self._new_id("message")
        file_ids: list[int] = []
        for name, data in files:
            file_id = self._new_id("disk")
            url = f"https://{self.host}/rest/1/TOKEN/download/?token=disk{file_id}"
            self.disk[file_id] = {"ID": str(file_id), "NAME": name, "SIZE": len(data), "DOWNLOAD_URL": url}
            self.files[url] = data
            file_ids.append(file_id)
        moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.messages.setdefault(dialog, []).append(
            {
                "id": message_id,
                "chat_id": 360293,
                "author_id": author,
                "date": moment.isoformat(timespec="seconds"),
                "text": text,
                "params": {"FILE_ID": file_ids} if file_ids else {},
            }
        )
        return message_id

    # ------------------------------------------------------------------ identidade e permissões do webhook (somente leitura)
    def _m_profile(self, p):
        return {"result": copy.deepcopy(self.profile)}

    def _m_scope(self, p):
        return {"result": list(self.scopes)}

    # ------------------------------------------------------------------ Canais Abertos / disco
    def _m_im_dialog_messages_get(self, p):
        rows = sorted(self.messages.get(p["DIALOG_ID"], []), key=lambda m: m["id"])
        limit = int(p.get("LIMIT", 50))
        if "FIRST_ID" in p:  # mais NOVAS que X, em ordem crescente (comportamento medido no portal real)
            page = [m for m in rows if m["id"] > int(p["FIRST_ID"])][:limit]
        elif "LAST_ID" in p:  # mais ANTIGAS que X, em ordem decrescente
            page = [m for m in reversed(rows) if m["id"] < int(p["LAST_ID"])][:limit]
        else:  # as mais recentes, em ordem decrescente
            page = list(reversed(rows))[:limit]
        files = []
        for message in page:
            for file_id in message["params"].get("FILE_ID", []):
                info = self.disk[file_id]
                ext = info["NAME"].rsplit(".", 1)[-1].lower() if "." in info["NAME"] else ""
                files.append({"id": file_id, "name": info["NAME"], "extension": ext, "size": info["SIZE"],
                              "type": "image" if ext in ("jpg", "jpeg", "png", "webp") else "file"})
        users = [copy.deepcopy(u) for u in self.users.values()]
        return {"result": {"chat_id": 360293, "messages": copy.deepcopy(page), "files": files, "users": users}}

    def _m_disk_file_get(self, p):
        info = self.disk.get(int(p["id"]))
        if info is None:
            raise BitrixError("NOT_FOUND", "File not found")
        return {"result": copy.deepcopy(info)}

    # ------------------------------------------------------------------ contatos
    def _phone_entry(self, value: str, value_type: str) -> dict[str, str]:
        return {"ID": str(self._new_id("phone")), "VALUE_TYPE": value_type, "VALUE": value, "TYPE_ID": "PHONE"}

    def _add_phone(self, contact: dict[str, Any], value: str, value_type: str) -> None:
        contact["PHONE"].append(self._phone_entry(value, value_type))
        if self.phone_variants:  # o portal expande o telefone em variantes (com/sem 55)
            digits = _digits(value)
            present = {_digits(p["VALUE"]) for p in contact["PHONE"]}
            variants = [digits] + (["55" + digits] if not digits.startswith("55") and len(digits) in (10, 11) else [])
            for variant in variants:
                if variant not in present:
                    contact["PHONE"].append(self._phone_entry(variant, value_type))
                    present.add(variant)

    def _apply_contact(self, contact: dict[str, Any], fields: dict[str, Any]) -> None:
        for key, value in fields.items():
            if key == "PHONE":
                for entry in value:
                    if entry.get("ID"):
                        for phone in contact["PHONE"]:
                            if phone["ID"] == str(entry["ID"]):
                                phone["VALUE"] = entry["VALUE"]
                    else:
                        self._add_phone(contact, entry["VALUE"], entry.get("VALUE_TYPE", "WORK"))
            elif key == "BIRTHDATE":
                contact["BIRTHDATE"] = f"{str(value)[:10]}T03:00:00+03:00" if value else ""
            else:
                contact[key] = "" if value is None else str(value)

    def _run_bp(self, contact: dict[str, Any]) -> None:
        if self.bp:  # BP #425: ucwords em nome, sobrenome e nome da mãe (observado: "MARCOS" -> "Marcos")
            for key in ("NAME", "LAST_NAME", MOTHER_KEY):
                if contact.get(key):
                    contact[key] = contact[key].title()

    @staticmethod
    def _matches(row: dict[str, Any], key: str, expected: Any) -> bool:
        actual = row.get(key, "")
        if key in ("NAME", "LAST_NAME"):
            return fold(str(actual)).strip() == fold(str(expected)).strip()
        return str(actual if actual is not None else "").strip() == str(expected).strip()

    @staticmethod
    def _select(row: dict[str, Any], select: list[str], id_key: str) -> dict[str, Any]:
        keys = select or list(row)
        out = {k: copy.deepcopy(row.get(k, "")) for k in keys}
        out[id_key] = row[id_key]
        return out

    def _m_crm_contact_fields(self, p):
        return {"result": copy.deepcopy(self.contact_fields)}

    def _m_crm_contact_list(self, p):
        rows = [c for c in self.contacts.values() if all(self._matches(c, k, v) for k, v in p.get("filter", {}).items())]
        return {"result": [self._select(c, p.get("select", []), "ID") for c in rows], "total": len(rows)}

    def _m_crm_contact_get(self, p):
        contact = self.contacts.get(int(p["id"]))
        if contact is None:
            raise BitrixError("NOT_FOUND", "Contact is not found")
        return {"result": copy.deepcopy(contact)}

    def _m_crm_contact_add(self, p):
        self.writes.append(("crm.contact.add", p))
        cid = self._new_id("contact")
        contact: dict[str, Any] = {"ID": str(cid), "NAME": "", "LAST_NAME": "", "BIRTHDATE": "", "PHONE": []}
        self._apply_contact(contact, p["fields"])
        self._run_bp(contact)
        self.contacts[cid] = contact
        return {"result": cid}

    def _m_crm_contact_update(self, p):
        self.writes.append(("crm.contact.update", p))
        contact = self.contacts[int(p["id"])]
        self._apply_contact(contact, p["fields"])
        self._run_bp(contact)
        return {"result": True}

    # ------------------------------------------------------------------ itens da SPA
    def _m_crm_item_fields(self, p):
        return {"result": {"fields": copy.deepcopy(self.item_fields)}}

    def _m_crm_category_list(self, p):
        return {"result": {"categories": copy.deepcopy(self.categories)}}

    def _m_crm_status_list(self, p):
        wanted = p.get("filter", {}).get("ENTITY_ID")
        return {"result": [copy.deepcopy(s) for s in self.stages if wanted in (None, s["ENTITY_ID"])]}

    def _m_crm_item_list(self, p):
        rows = [i for i in self.items.values() if all(self._matches(i, k, v) for k, v in p.get("filter", {}).items())]
        return {"result": {"items": [self._select(i, p.get("select", []), "id") for i in rows]}, "total": len(rows)}

    def _m_crm_item_get(self, p):
        item = self.items.get(int(p["id"]))
        if item is None:
            raise BitrixError("NOT_FOUND", "Item is not found")
        return {"result": {"item": copy.deepcopy(item)}}

    def _m_crm_item_add(self, p):
        assert p["entityTypeId"] == 1146
        self.writes.append(("crm.item.add", p))
        iid = self.seed_item(**p["fields"])
        item = self.items[iid]
        if self.robots:  # automação da casa: título "NOME ➡ TELEFONE ➡ ID", etapa por rodízio e responsável
            contact = self.contacts.get(int(item["contactId"])) if item.get("contactId") else None
            name = f"{contact['NAME']} {contact['LAST_NAME']}".strip() if contact else ""
            phone = contact["PHONE"][0]["VALUE"] if contact and contact["PHONE"] else ""
            item["title"] = f"{name} ➡ {phone} ➡ {iid}"
            item["stageId"], item["assignedById"] = [("DT1146_101:UC_2WIOW1", 111919), ("DT1146_101:UC_SL43B7", 118809)][self._round_robin % 2]
            self._round_robin += 1
        return {"result": {"item": copy.deepcopy(item)}}

    def _m_crm_item_update(self, p):
        self.writes.append(("crm.item.update", p))
        item = self.items[int(p["id"])]
        item.update(p["fields"])
        return {"result": {"item": copy.deepcopy(item)}}

    # ------------------------------------------------------------------ timeline
    def _m_crm_timeline_comment_add(self, p):
        self.writes.append(("crm.timeline.comment.add", p))
        fields = p["fields"]
        comment = {"ID": str(self._new_id("comment")), **{k: str(v) for k, v in fields.items()}}
        self.comments.append(comment)
        return {"result": int(comment["ID"])}

    def _m_crm_timeline_comment_list(self, p):
        flt = p.get("filter", {})
        rows = [c for c in self.comments if all(str(c.get(k)) == str(v) for k, v in flt.items())]
        return {"result": copy.deepcopy(rows)}
