class Node {
  int elem;
  Node next;
  static Node static_next;

  Node swapNode() {

    static_next = new Node();
    static_next.next = new Node();
    static_next.next.next = new Node();
    if (next != null)
      if (elem > next.elem) {
        assert next != null;
        Node t = next;
        next = t.next;
        t.next = this;
        return t;
      }
    return this;
  }
}
