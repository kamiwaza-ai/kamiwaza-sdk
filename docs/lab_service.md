# Lab Service

The Lab Service allows you to manage lab environments.

## Methods

### list_labs()

Lists all labs.

```python
labs = client.lab.list_labs()
```

### create_lab(username: str, resources: Optional[Dict[str, str]] = None)

Creates a new lab environment.

```python
new_lab = client.lab.create_lab("user123", resources={"cpu": "2", "memory": "8Gi"})
```

### get_lab(lab_id: UUID)

Gets details of a specific lab.

```python
lab = client.lab.get_lab("lab_id_here")
```

### delete_lab(lab_id: UUID)

Deletes a lab.

```python
client.lab.delete_lab("lab_id_here")
```
