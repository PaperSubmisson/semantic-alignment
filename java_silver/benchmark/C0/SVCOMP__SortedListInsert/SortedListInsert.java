public class SortedListInsert {

  private static class List {
    public int x;
    public List next;

    private static final int SENTINEL = Integer.MAX_VALUE;

    private List(int x, List next) {
      this.x = x;
      this.next = next;
    }

    public List() {
      this(SENTINEL, null);
    }

    public void insert(int data) {
      if (data > this.x) {
        next.insert(data);
      } else {
        next = new List(x, next);
        x = data;
      }
    }
  }
  
}
