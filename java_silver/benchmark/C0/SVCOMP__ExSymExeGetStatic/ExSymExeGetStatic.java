class Main {

  public static void main(String[] args) {
    SNode sn = new SNode();
    SNode sn2 = sn.swap();
  }
}

class SNode {
  int elem;
  SNode next;
  static SNode head; // = new SNode(); //change is here

  SNode swap() {
    if (head != null) {
      assert false;
      System.out.println("head is not null");
    } else {
      System.out.println("head is null");
    }
    return this;
  }
}
